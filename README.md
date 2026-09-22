# pg-jev

[![Test](https://github.com/mejiasd3v/pg-jev/actions/workflows/test.yml/badge.svg)](https://github.com/mejiasd3v/pg-jev/actions/workflows/test.yml)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-extension-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/mejiasd3v/pg-jev?logo=github)](https://github.com/mejiasd3v/pg-jev)

Typed AI decisions inside PostgreSQL. `prompt_jev` evaluates text with Jev and
returns a probability, choice, score, or set of answers as `jsonb`.

```sql
SELECT prompt_jev(
  'I was charged twice.',
  'Which team should handle this?',
  choice => '["billing", "technical", "sales"]'::jsonb
);
```

```json
{
  "choice": "billing",
  "probabilities": [
    {"value": "billing", "probability": 0.94},
    {"value": "technical", "probability": 0.04},
    {"value": "sales", "probability": 0.02}
  ],
  "confidence": 0.91
}
```

## Features

- Noul probability, Choice, and Score questions
- Multiple questions in one request
- TypeSafe, Vercel AI Gateway, and Cloudflare AI Gateway
- MotherDuck-style named SQL arguments
- No runtime Python packages

## Install

Requires PostgreSQL with `plpython3u` and outbound HTTPS access.

```sh
make
sudo make install
```

```sql
CREATE EXTENSION plpython3u;
CREATE EXTENSION pg_prompt_jev;
GRANT EXECUTE ON FUNCTION prompt_jev(text, text, jsonb, jsonb, jsonb, jsonb)
TO app_user;
```

The extension revokes public execution because inputs leave the database.

## Configure

Set these variables in the PostgreSQL server environment, then restart it:

| Provider | Variables |
| --- | --- |
| TypeSafe, default | `TYPESAFE_API_KEY` |
| Vercel AI Gateway | `PROMPT_JEV_PROVIDER=vercel`, `AI_GATEWAY_API_KEY` |
| Cloudflare AI Gateway | `PROMPT_JEV_PROVIDER=cloudflare`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_AI_GATEWAY_ID` |

For local testing, use session settings:

```sql
SET jev.provider = 'vercel';
SET jev.api_key = 'your-key';
```

Do not persist API keys with `ALTER SYSTEM`.

## More examples

```sql
-- Probability from 0 to 1
SELECT prompt_jev(message, 'Does this request a refund?');

-- Ordered score
SELECT prompt_jev(
  message,
  'How urgent is this?',
  score => '["low", "medium", "high"]'::jsonb
);

-- Several decisions in one request
SELECT prompt_jev(
  message,
  questions => '{
    "refund": {"type": "noul", "instructions": "Does this request a refund?"},
    "team": {
      "type": "choice",
      "instructions": "Which team owns this?",
      "criteria": ["billing", "technical", "sales"]
    }
  }'::jsonb
);
```

`NULL` input returns `NULL` without making a request. PostgreSQL requires a
fixed function return type, so every mode returns `jsonb`. Calls are not
batched; store results if they will be reused.

## Test

```sh
make test
```

## Release

Update `default_version` in `pg_prompt_jev.control`, the `DATA` path in the
`Makefile`, and add the matching versioned SQL file. A successful push or merge
to `main` creates the tag, GitHub release, and source archive.

## License

[MIT](LICENSE)
