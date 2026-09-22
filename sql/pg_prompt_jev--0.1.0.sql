CREATE FUNCTION prompt_jev(
    "input" text,
    instructions text DEFAULT NULL,
    noul jsonb DEFAULT NULL,
    choice jsonb DEFAULT NULL,
    score jsonb DEFAULT NULL,
    questions jsonb DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpython3u
VOLATILE
PARALLEL UNSAFE
AS $PY$
import json
import os
import urllib.error
import urllib.request

if input is None:
    return None

modes = [(name, value) for name, value in (
    ("noul", noul), ("choice", choice), ("score", score)
) if value is not None]
if questions is not None and (instructions is not None or modes):
    plpy.error('prompt_jev "questions" cannot be combined with other question arguments')
if questions is None and not instructions:
    plpy.error("prompt_jev requires instructions")
if len(modes) > 1:
    plpy.error('prompt_jev "choice", "score", and "noul" cannot be combined')


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def normalize_criteria(kind, criteria):
    if criteria is None:
        if kind == "noul":
            return None, None
        plpy.error(f'prompt_jev requires criteria for type "{kind}"')

    criteria = decode(criteria)
    if isinstance(criteria, dict):
        labels = list(criteria)
        api_criteria = criteria
    elif isinstance(criteria, list):
        labels = []
        descriptions = []
        for item in criteria:
            if isinstance(item, str):
                label, description = item, None
            elif isinstance(item, dict):
                label = item.get("label")
                description = item.get("description")
            else:
                plpy.error("prompt_jev criteria must contain strings or label objects")
            if not isinstance(label, str) or not label:
                plpy.error("prompt_jev criteria labels must be non-empty strings")
            if description is not None and (not isinstance(description, str) or not description):
                plpy.error("prompt_jev criteria descriptions must be null or non-empty strings")
            labels.append(label)
            descriptions.append(description)
        if kind == "score":
            api_criteria = [
                f"{label}: {description}" if description else label
                for label, description in zip(labels, descriptions)
            ]
        elif kind == "noul":
            api_criteria = {
                label: description or label
                for label, description in zip(labels, descriptions)
            }
        else:
            api_criteria = dict(zip(labels, descriptions))
    else:
        plpy.error("prompt_jev criteria must be a JSON array or object")

    if len(labels) != len(set(labels)):
        plpy.error("prompt_jev criteria labels must be unique")
    if kind == "noul" and set(labels) != {"true", "false"}:
        plpy.error('prompt_jev noul criteria require exactly the labels "true" and "false"')
    limits = {"choice": (2, 255), "score": (2, 10)}
    if kind in limits and not limits[kind][0] <= len(labels) <= limits[kind][1]:
        low, high = limits[kind]
        plpy.error(f'prompt_jev type "{kind}" requires between {low} and {high} criteria')
    return api_criteria, labels


def normalize_question(question):
    if not isinstance(question, dict):
        plpy.error("prompt_jev questions must contain JSON objects")
    kind = question.get("type")
    if kind not in ("noul", "choice", "score"):
        plpy.error('prompt_jev question type must be "noul", "choice", or "score"')
    if question.get("instructions") in (None, ""):
        plpy.error("prompt_jev question requires instructions")
    normalized = {"type": kind, "instructions": question["instructions"]}
    api_criteria, labels = normalize_criteria(kind, question.get("criteria"))
    if api_criteria is not None:
        normalized["criteria"] = api_criteria
    return normalized, labels


def shape_answer(answer, labels):
    kind = answer.get("type")
    if kind == "noul":
        return answer["noul"]
    if labels is None:
        return answer
    probabilities = answer.get("probabilities", {})
    if kind == "choice":
        return {
            "choice": answer["choice"],
            "probabilities": [
                {"value": label, "probability": probabilities.get(label, 0.0)}
                for label in labels
            ],
            "confidence": answer["confidence"],
        }
    return {
        "score": answer["score"],
        "probabilities": [
            {"index": index, "value": label, "probability": probabilities.get(str(index), 0.0)}
            for index, label in enumerate(labels)
        ],
        "confidence": answer["confidence"],
    }

if questions is not None:
    supplied = decode(questions)
    if not isinstance(supplied, dict) or not supplied:
        plpy.error("prompt_jev questions must be a non-empty JSON object")
    api_questions = {}
    labels_by_name = {}
    for name, question in supplied.items():
        api_questions[name], labels_by_name[name] = normalize_question(question)
else:
    kind, criteria = modes[0] if modes else ("noul", None)
    api_criteria, labels = normalize_criteria(kind, criteria)
    api_questions = {"answer": {"type": kind, "instructions": instructions}}
    if api_criteria is not None:
        api_questions["answer"]["criteria"] = api_criteria
    labels_by_name = {"answer": labels}

def setting(environment_name, guc_name):
    value = os.environ.get(environment_name)
    if value:
        return value
    rows = plpy.execute(f"SELECT current_setting('{guc_name}', true) AS value")
    return rows[0]["value"] if rows else None


provider = (setting("PROMPT_JEV_PROVIDER", "jev.provider") or "typesafe").lower()
provider_settings = {
    "typesafe": ("TYPESAFE_API_KEY", "https://api.typesafe.ai"),
    "vercel": ("AI_GATEWAY_API_KEY", "https://ai-gateway.vercel.sh/typesafe"),
    "cloudflare": ("CLOUDFLARE_API_TOKEN", None),
}
if provider not in provider_settings:
    plpy.error('prompt_jev provider must be "typesafe", "vercel", or "cloudflare"')

key_name, default_base_url = provider_settings[provider]
api_key = setting(key_name, "jev.api_key")
if not api_key:
    plpy.error(f"prompt_jev provider {provider} requires {key_name} or jev.api_key")

headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
if provider == "cloudflare":
    account_id = setting("CLOUDFLARE_ACCOUNT_ID", "jev.cloudflare_account_id")
    if not account_id:
        plpy.error("prompt_jev provider cloudflare requires CLOUDFLARE_ACCOUNT_ID or jev.cloudflare_account_id")
    gateway_id = setting("CLOUDFLARE_AI_GATEWAY_ID", "jev.cloudflare_gateway_id")
    if gateway_id:
        headers["cf-aig-gateway-id"] = gateway_id
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
    body = {
        "model": os.environ.get("CLOUDFLARE_JEV_MODEL", "typesafe/jev"),
        "input": {"state": input, "questions": api_questions},
    }
else:
    base_url_name = "TYPESAFE_BASE_URL" if provider == "typesafe" else "VERCEL_AI_GATEWAY_BASE_URL"
    url = os.environ.get(base_url_name, default_base_url).rstrip("/") + "/v1/systemone"
    body = {
        "state": input,
        "model": os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest"),
        "questions": api_questions,
    }

request = urllib.request.Request(
    url,
    data=json.dumps(body).encode(),
    headers=headers,
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
except urllib.error.HTTPError as error:
    detail = error.read(1000).decode("utf-8", "replace")
    plpy.error(f"prompt_jev API returned HTTP {error.code}: {detail}")
except (urllib.error.URLError, TimeoutError) as error:
    plpy.error(f"prompt_jev API request failed: {error}")

answers = result.get("answers")
if not isinstance(answers, dict):
    plpy.error("prompt_jev API returned an invalid response")
shaped = {
    name: shape_answer(answer, labels_by_name.get(name))
    for name, answer in answers.items()
}
return json.dumps(shaped if questions is not None else shaped["answer"])
$PY$;

REVOKE ALL ON FUNCTION prompt_jev(text, text, jsonb, jsonb, jsonb, jsonb) FROM PUBLIC;

COMMENT ON FUNCTION prompt_jev(text, text, jsonb, jsonb, jsonb, jsonb) IS
'Send text and closed questions to Jev; grant EXECUTE only to roles allowed to send data to TypeSafe.';
