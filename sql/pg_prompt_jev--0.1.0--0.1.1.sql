CREATE OR REPLACE FUNCTION prompt_jev(
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
import email.utils
import hashlib
import http.client
import ipaddress
import json
import math
import os
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

if input is None:
    return None


def guc(name):
    rows = plpy.execute(f"SELECT pg_catalog.current_setting('{name}', true) AS value")
    return rows[0]["value"] if rows else None


def configuration_error(message):
    plpy.error(message, sqlstate="JV001")


def policy(environment_name, guc_name, default, integer=False, allow_zero=False):
    def parse(value):
        try:
            parsed = int(value) if integer else float(value)
        except (TypeError, ValueError, OverflowError):
            configuration_error(f"prompt_jev {guc_name} must be a positive number")
        if not math.isfinite(parsed) or parsed < 0 or parsed == 0 and not allow_zero:
            configuration_error(f"prompt_jev {guc_name} must be a positive number")
        return parsed

    ceiling = parse(os.environ.get(environment_name, default))
    requested = guc(guc_name)
    if requested in (None, ""):
        return ceiling
    requested = parse(requested)
    if requested > ceiling:
        configuration_error(f"prompt_jev {guc_name} exceeds the administrator ceiling")
    return requested


max_input_bytes = policy("PROMPT_JEV_MAX_INPUT_BYTES", "jev.input_bytes", 256 * 1024, True)
max_request_bytes = policy("PROMPT_JEV_MAX_REQUEST_BYTES", "jev.request_bytes", 1024 * 1024, True)
max_response_bytes = policy("PROMPT_JEV_MAX_RESPONSE_BYTES", "jev.response_bytes", 1024 * 1024, True)
max_questions = policy("PROMPT_JEV_MAX_QUESTIONS", "jev.question_limit", 64, True)
max_json_depth = policy("PROMPT_JEV_MAX_JSON_DEPTH", "jev.json_depth", 64, True)
connect_timeout = policy("PROMPT_JEV_MAX_CONNECT_SECONDS", "jev.connect_timeout", 2)
read_timeout = policy("PROMPT_JEV_MAX_READ_SECONDS", "jev.read_timeout", 3)
total_timeout = policy("PROMPT_JEV_MAX_TOTAL_SECONDS", "jev.total_timeout", 10)
max_retries = policy(
    "PROMPT_JEV_MAX_RETRIES", "jev.retries", 0, integer=True, allow_zero=True
)
max_retry_after = policy(
    "PROMPT_JEV_MAX_RETRY_AFTER_SECONDS", "jev.retry_after", 1
)
max_connection_age = policy(
    "PROMPT_JEV_MAX_CONNECTION_AGE_SECONDS", "jev.connection_age", 60
)
if max_retries > 1:
    configuration_error("prompt_jev allows at most one retry")

if len(input.encode("utf-8")) > max_input_bytes:
    plpy.error("prompt_jev input exceeds the configured byte limit", sqlstate="22023")


def input_error(message):
    plpy.error(message, sqlstate="22023")


modes = [(name, value) for name, value in (
    ("noul", noul), ("choice", choice), ("score", score)
) if value is not None]
if questions is not None and (instructions is not None or modes):
    input_error('prompt_jev "questions" cannot be combined with other question arguments')
if questions is None and instructions is None:
    input_error("prompt_jev requires instructions")
if len(modes) > 1:
    input_error('prompt_jev "choice", "score", and "noul" cannot be combined')


def decode_argument(value, name):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (RecursionError, TypeError, ValueError):
        input_error(f'prompt_jev "{name}" must be valid JSON')


def valid_content(value):
    return (
        isinstance(value, str) and bool(value.strip())
        or isinstance(value, (dict, list)) and bool(value)
    )


def valid_description(value):
    return value is None or valid_content(value)


def nesting_depth(value):
    deepest = 0
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict):
            deepest = max(deepest, depth + 1)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            deepest = max(deepest, depth + 1)
            stack.extend((item, depth + 1) for item in current)
    return deepest


def normalize_criteria(kind, criteria):
    if criteria is None:
        if kind == "noul":
            return None, None
        input_error(f'prompt_jev requires criteria for type "{kind}"')
    if kind == "score" and isinstance(criteria, dict):
        input_error("prompt_jev score criteria must be an ordered JSON array")

    if isinstance(criteria, dict):
        labels = list(criteria)
        descriptions = list(criteria.values())
    elif isinstance(criteria, list):
        labels = []
        descriptions = []
        for item in criteria:
            if isinstance(item, str):
                label, description = item, None
            elif isinstance(item, dict):
                unknown = set(item) - {"label", "description"}
                if unknown:
                    input_error("prompt_jev criteria label objects contain unknown fields")
                label = item.get("label")
                description = item.get("description")
            else:
                input_error("prompt_jev criteria must contain strings or label objects")
            labels.append(label)
            descriptions.append(description)
    else:
        input_error("prompt_jev criteria must be a JSON array or object")

    if any(not isinstance(label, str) or not label.strip() for label in labels):
        input_error("prompt_jev criteria labels must be non-empty strings")
    if any(not valid_description(description) for description in descriptions):
        input_error(
            "prompt_jev criteria descriptions must be null or non-empty strings, objects, or arrays"
        )
    if len(labels) != len(set(labels)):
        input_error("prompt_jev criteria labels must be unique")
    if kind == "noul" and set(labels) != {"true", "false"}:
        input_error('prompt_jev noul criteria require exactly the labels "true" and "false"')
    limits = {"choice": (2, 255), "score": (2, 10)}
    if kind in limits and not limits[kind][0] <= len(labels) <= limits[kind][1]:
        low, high = limits[kind]
        input_error(f'prompt_jev type "{kind}" requires between {low} and {high} criteria')

    if kind == "score":
        api_criteria = [
            label if description is None else {"label": label, "description": description}
            for label, description in zip(labels, descriptions)
        ]
    elif kind == "noul":
        api_criteria = {
            label: label if description is None else description
            for label, description in zip(labels, descriptions)
        }
    else:
        api_criteria = dict(zip(labels, descriptions))
    return api_criteria, labels


def normalize_question(question):
    if not isinstance(question, dict):
        input_error("prompt_jev questions must contain JSON objects")
    unknown = set(question) - {"type", "instructions", "criteria"}
    if unknown:
        input_error("prompt_jev question contains unknown fields")
    kind = question.get("type")
    if kind not in ("noul", "choice", "score"):
        input_error('prompt_jev question type must be "noul", "choice", or "score"')
    if not valid_content(question.get("instructions")):
        input_error("prompt_jev question requires non-empty string, object, or array instructions")
    normalized = {"type": kind, "instructions": question["instructions"]}
    api_criteria, labels = normalize_criteria(kind, question.get("criteria"))
    if api_criteria is not None:
        normalized["criteria"] = api_criteria
    return normalized, {"type": kind, "labels": labels}


PROBABILITY_TOLERANCE = 1e-6
SCORE_TOLERANCE = 0.02


def response_error(message="prompt_jev API returned an invalid response"):
    plpy.error(message, sqlstate="JV006")


def finite_number(value, low, high):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and low <= value <= high
    )


def validate_probabilities(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        response_error()
    if any(not finite_number(probability, 0, 1) for probability in value.values()):
        response_error()
    if abs(sum(value.values()) - 1) > PROBABILITY_TOLERANCE:
        response_error()
    return value


def validate_answer(answer, spec):
    if not isinstance(answer, dict) or answer.get("type") != spec["type"]:
        response_error()
    kind = spec["type"]
    if kind == "noul":
        value = answer.get("noul")
        if not finite_number(value, 0, 1):
            response_error()
        return value

    labels = spec["labels"]
    confidence = answer.get("confidence")
    if not finite_number(confidence, 0, 1):
        response_error()
    if kind == "choice":
        choice = answer.get("choice")
        probabilities = validate_probabilities(answer.get("probabilities"), labels)
        if choice not in labels:
            response_error()
        if probabilities[choice] + PROBABILITY_TOLERANCE < max(probabilities.values()):
            response_error()
        return {
            "choice": choice,
            "probabilities": [
                {"value": label, "probability": probabilities[label]}
                for label in labels
            ],
            "confidence": confidence,
        }

    keys = [str(index) for index in range(len(labels))]
    probabilities = validate_probabilities(answer.get("probabilities"), keys)
    legend = answer.get("legend")
    if (
        not isinstance(legend, dict)
        or set(legend) != set(keys)
        or any(not isinstance(value, str) or not value for value in legend.values())
    ):
        response_error()
    score = answer.get("score")
    if not finite_number(score, 0, len(labels) - 1):
        response_error()
    expected_score = sum(index * probabilities[str(index)] for index in range(len(labels)))
    if abs(score - expected_score) > SCORE_TOLERANCE:
        response_error()
    return {
        "score": score,
        "probabilities": [
            {"index": index, "value": label, "probability": probabilities[str(index)]}
            for index, label in enumerate(labels)
        ],
        "confidence": confidence,
    }


def parse_response(data):
    def object_without_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def reject_constant(_value):
        raise ValueError("nonstandard number")

    try:
        result = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
        )
    except (RecursionError, UnicodeDecodeError, TypeError, ValueError):
        response_error()
    if not isinstance(result, dict) or nesting_depth(result) > max_json_depth:
        response_error()
    usage = result.get("usage")
    if (
        not isinstance(result.get("model"), str)
        or not result["model"]
        or not isinstance(usage, dict)
        or any(
            not isinstance(usage.get(name), int)
            or isinstance(usage.get(name), bool)
            or usage[name] < 0
            for name in ("input_tokens", "output_tokens")
        )
    ):
        response_error()
    answers = result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(specs_by_name):
        response_error()
    return answers

if questions is not None:
    supplied = decode_argument(questions, "questions")
    if not isinstance(supplied, dict) or not supplied:
        input_error("prompt_jev questions must be a non-empty JSON object")
    if len(supplied) > max_questions:
        input_error("prompt_jev questions exceed the configured count limit")
    if any(not isinstance(name, str) or not name.strip() for name in supplied):
        input_error("prompt_jev question names must be non-empty strings")
    api_questions = {}
    specs_by_name = {}
    for name, question in supplied.items():
        api_questions[name], specs_by_name[name] = normalize_question(question)
else:
    if not valid_content(instructions) or not isinstance(instructions, str):
        input_error("prompt_jev requires non-empty text instructions")
    kind, criteria = modes[0] if modes else ("noul", None)
    if criteria is not None:
        criteria = decode_argument(criteria, kind)
    api_criteria, labels = normalize_criteria(kind, criteria)
    api_questions = {"answer": {"type": kind, "instructions": instructions}}
    if api_criteria is not None:
        api_questions["answer"]["criteria"] = api_criteria
    specs_by_name = {"answer": {"type": kind, "labels": labels}}

def setting(environment_name, guc_name):
    value = os.environ.get(environment_name)
    return value if value else guc(guc_name)


provider = (setting("PROMPT_JEV_PROVIDER", "jev.provider") or "typesafe").lower()
allowed_providers = {
    value.strip().lower()
    for value in os.environ.get(
        "PROMPT_JEV_ALLOWED_PROVIDERS", "typesafe,vercel,cloudflare"
    ).split(",")
    if value.strip()
}
if provider not in ("typesafe", "vercel", "cloudflare"):
    configuration_error('prompt_jev provider must be "typesafe", "vercel", or "cloudflare"')
if provider not in allowed_providers:
    configuration_error(f"prompt_jev provider {provider} is not allowed by administrator policy")


def safe_header(name, value):
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        configuration_error(f"prompt_jev {name} contains prohibited control characters")
    return value


def validate_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        plpy.error("prompt_jev provider URL is invalid", sqlstate="JV007")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or parsed.scheme not in ("http", "https")
        or port is not None and not 1 <= port <= 65535
    ):
        plpy.error("prompt_jev provider URL is invalid", sqlstate="JV007")
    if parsed.scheme == "http":
        hostname = parsed.hostname.rstrip(".").lower()
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if not loopback or os.environ.get("PROMPT_JEV_ALLOW_INSECURE_LOOPBACK") != "1":
            plpy.error("prompt_jev provider URL requires HTTPS", sqlstate="JV007")
    return value


def authorization(environment_name):
    api_key = setting(environment_name, "jev.api_key")
    if not api_key:
        configuration_error(
            f"prompt_jev provider {provider} requires {environment_name} or jev.api_key"
        )
    return {
        "Authorization": "Bearer " + safe_header("API key", api_key),
        "Content-Type": "application/json",
    }


def model(value, default):
    value = value or default
    if not isinstance(value, str) or not value.strip():
        configuration_error("prompt_jev model must be a non-empty string")
    return value


def typesafe_adapter():
    return (
        os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
        + "/v1/systemone",
        authorization("TYPESAFE_API_KEY"),
        {
            "state": input,
            "model": model(setting("TYPESAFE_DEFAULT_MODEL", "jev.typesafe_model"), "jev-latest"),
            "questions": api_questions,
        },
    )


def vercel_adapter():
    selected_model = (
        os.environ.get("VERCEL_JEV_MODEL")
        or os.environ.get("TYPESAFE_DEFAULT_MODEL")
        or guc("jev.vercel_model")
    )
    return (
        os.environ.get(
            "VERCEL_AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/typesafe"
        ).rstrip("/") + "/v1/systemone",
        authorization("AI_GATEWAY_API_KEY"),
        {
            "state": input,
            "model": model(selected_model, "typesafe-ai/jev"),
            "questions": api_questions,
        },
    )


def cloudflare_adapter():
    account_id = setting("CLOUDFLARE_ACCOUNT_ID", "jev.cloudflare_account_id")
    if not account_id:
        configuration_error(
            "prompt_jev provider cloudflare requires CLOUDFLARE_ACCOUNT_ID "
            "or jev.cloudflare_account_id"
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]+", account_id):
        configuration_error("prompt_jev Cloudflare account ID must be one path segment")
    headers = authorization("CLOUDFLARE_API_TOKEN")
    gateway_id = setting("CLOUDFLARE_AI_GATEWAY_ID", "jev.cloudflare_gateway_id")
    if gateway_id:
        headers["cf-aig-gateway-id"] = safe_header("Cloudflare gateway ID", gateway_id)
    return (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run",
        headers,
        {
            "model": model(
                setting("CLOUDFLARE_JEV_MODEL", "jev.cloudflare_model"), "typesafe/jev"
            ),
            "input": {"state": input, "questions": api_questions},
        },
    )


url, headers, body = {
    "typesafe": typesafe_adapter,
    "vercel": vercel_adapter,
    "cloudflare": cloudflare_adapter,
}[provider]()
started = time.monotonic()
url = validate_url(url)
if nesting_depth(body) > max_json_depth:
    input_error("prompt_jev request exceeds the configured JSON nesting limit")
try:
    request_data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
except (TypeError, ValueError):
    input_error("prompt_jev request contains an unsupported JSON value")
if len(request_data) > max_request_bytes:
    input_error("prompt_jev request exceeds the configured byte limit")

request = urllib.request.Request(
    url,
    data=request_data,
    headers=headers,
    method="POST",
)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request_id(headers):
    for name in ("X-Request-ID", "Request-ID", "CF-Ray"):
        value = headers.get(name)
        if value and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
            return value
    return None


def timeout_error():
    plpy.execute("SELECT 1")
    plpy.error("prompt_jev API request exceeded its configured time budget", sqlstate="JV002")


def read_response(response):
    encoding = response.headers.get("Content-Encoding")
    if encoding and encoding.lower() != "identity":
        response_error()
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared = int(declared)
        except ValueError:
            response_error()
        if declared < 0 or declared > max_response_bytes:
            response_error()

    data = bytearray()
    reader = getattr(response, "read1", response.read)
    while True:
        if time.monotonic() - started >= total_timeout:
            timeout_error()
        chunk = reader(min(65536, max_response_bytes + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_response_bytes:
            response_error()
        if time.monotonic() - started >= total_timeout:
            timeout_error()
    if declared is not None and len(data) != declared:
        response_error()
    return bytes(data)


def retry_delay(headers, attempt):
    value = headers.get("Retry-After")
    delay = None
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                pass
    if delay is None or not math.isfinite(delay):
        delay = 0.1 * (2 ** (attempt - 1)) + random.random() * 0.05
    return min(max(delay, 0), max_retry_after)


def http_error(error):
    status = error.code
    identifier = request_id(error.headers)
    error.close()
    if 300 <= status < 400:
        category, sqlstate = "redirect", "JV007"
    elif status in (401, 403):
        category, sqlstate = "authentication", "JV003"
    elif status == 429:
        category, sqlstate = "rate_limit", "JV004"
    else:
        category, sqlstate = "upstream", "JV005"
    detail = f" request_id={identifier}" if identifier else ""
    plpy.error(
        f"prompt_jev API request failed: provider={provider} category={category} "
        f"status={status}{detail}",
        sqlstate=sqlstate,
    )


parsed_url = urllib.parse.urlsplit(url)
proxies = urllib.request.getproxies()
proxy_url = None if urllib.request.proxy_bypass(parsed_url.hostname) else proxies.get(parsed_url.scheme)
role_rows = plpy.execute("SELECT CURRENT_USER AS value")
role = role_rows[0]["value"] if role_rows else None
connection_key = (
    1,
    provider,
    url,
    role,
    hashlib.sha256(headers["Authorization"].encode()).hexdigest(),
    body["model"],
    proxy_url,
    os.environ.get("SSL_CERT_FILE"),
    os.environ.get("SSL_CERT_DIR"),
)


def drop_connection(connection=None):
    cached = SD.get("http_connection")
    if cached and (connection is None or cached["connection"] is connection):
        cached["connection"].close()
        SD.pop("http_connection", None)
    elif connection is not None:
        connection.close()


def direct_response(timeout):
    cached = SD.get("http_connection")
    if cached and (
        cached["key"] != connection_key
        or time.monotonic() - cached["created"] >= max_connection_age
    ):
        drop_connection()
        cached = None
    connection = cached["connection"] if cached else None
    if connection is None:
        connection_type = (
            http.client.HTTPSConnection
            if parsed_url.scheme == "https"
            else http.client.HTTPConnection
        )
        options = {"timeout": timeout}
        if parsed_url.scheme == "https":
            options["context"] = ssl.create_default_context()
        connection = connection_type(parsed_url.hostname, parsed_url.port, **options)
    else:
        connection.timeout = timeout
        if connection.sock:
            connection.sock.settimeout(timeout)
    path = parsed_url.path or "/"
    try:
        connection.request("POST", path, body=request_data, headers=headers)
        response = connection.getresponse()
        if connection.sock:
            connection.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except BaseException:
        drop_connection(connection)
        raise
    if response.status >= 300:
        error = urllib.error.HTTPError(
            url, response.status, response.reason, response.headers, response
        )
        drop_connection(connection)
        raise error
    return response, connection, cached is not None


opener = urllib.request.build_opener(NoRedirect()) if proxy_url else None
attempt = 0
connection = None
reused = False
reusable = False
while True:
    attempt += 1
    remaining = total_timeout - (time.monotonic() - started)
    if remaining <= 0:
        timeout_error()
    timeout = min(connect_timeout, read_timeout, remaining)
    try:
        if opener:
            response = opener.open(request, timeout=timeout)
        else:
            response, connection, reused = direct_response(timeout)
        with response:
            response_data = read_response(response)
            reusable = connection is not None and not response.will_close
        break
    except urllib.error.HTTPError as error:
        retryable = error.code in (429, 502, 503, 504, 529)
        remaining = total_timeout - (time.monotonic() - started)
        if retryable and attempt <= max_retries:
            delay = retry_delay(error.headers, attempt)
            error.close()
            if delay < remaining:
                time.sleep(delay)
                continue
        http_error(error)
    except TimeoutError:
        drop_connection(connection)
        timeout_error()
    except urllib.error.URLError as error:
        drop_connection(connection)
        if isinstance(error.reason, TimeoutError):
            timeout_error()
        plpy.error(
            f"prompt_jev API request failed: provider={provider} category=transport",
            sqlstate="JV005",
        )
    except ssl.SSLError:
        drop_connection(connection)
        plpy.error(
            f"prompt_jev API request failed: provider={provider} category=tls",
            sqlstate="JV007",
        )
    except http.client.IncompleteRead:
        drop_connection(connection)
        response_error()
    except (OSError, http.client.HTTPException):
        drop_connection(connection)
        plpy.error(
            f"prompt_jev API request failed: provider={provider} category=transport",
            sqlstate="JV005",
        )
    except BaseException:
        drop_connection(connection)
        raise

try:
    answers = parse_response(response_data)
    shaped = {
        name: validate_answer(answer, specs_by_name[name])
        for name, answer in answers.items()
    }
except BaseException:
    drop_connection(connection)
    raise

if connection is not None:
    if reusable:
        if not reused:
            SD["http_connection"] = {
                "key": connection_key,
                "connection": connection,
                "created": time.monotonic(),
            }
    else:
        drop_connection(connection)
return json.dumps(shaped if questions is not None else shaped["answer"], allow_nan=False)
$PY$;

COMMENT ON FUNCTION prompt_jev(text, text, jsonb, jsonb, jsonb, jsonb) IS
'Send text and closed questions to a configured Jev provider; grant EXECUTE only to roles allowed to export that data.';
