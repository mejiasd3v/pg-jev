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

Requires PostgreSQL with `plpython3u` and outbound HTTPS access. CI tests
PostgreSQL 14 through 18.

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

The extension revokes public execution because inputs leave the database. The
function keeps its six-argument signature and is `VOLATILE`, `PARALLEL UNSAFE`,
security invoker, and not `STRICT`.

## Configure

Set these variables in the PostgreSQL server environment, then restart it:

| Provider | Variables |
| --- | --- |
| TypeSafe, default | `TYPESAFE_API_KEY`, optional `TYPESAFE_DEFAULT_MODEL` |
| Vercel AI Gateway | `PROMPT_JEV_PROVIDER=vercel`, `AI_GATEWAY_API_KEY`, optional `VERCEL_JEV_MODEL` |
| Cloudflare AI Gateway | `PROMPT_JEV_PROVIDER=cloudflare`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, optional `CLOUDFLARE_AI_GATEWAY_ID` and `CLOUDFLARE_JEV_MODEL` |

For local testing, use session settings:

```sql
SET jev.provider = 'vercel';
SET jev.api_key = 'your-key';
```

Do not persist API keys with `ALTER SYSTEM`. Server environment values take
precedence over session settings. See [configuration](docs/configuration.md),
[resource limits](docs/limits.md), [errors](docs/errors.md), and
[security](SECURITY.md).

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

`NULL` input returns `NULL` without validating other arguments or making a
request. PostgreSQL requires a fixed function return type, so every mode returns
`jsonb`. Successful output shapes remain answer-only.

Each non-NULL call performs external work inside the current statement. A
rollback cannot retract sent data or provider cost, and `EXPLAIN ANALYZE` can
make requests. Calls are not batched across rows. Prefer one multi-question call
for the same input, bound candidate rows before evaluation, and store results
that will be reused. See [operations](docs/operations.md).

## Test

```sh
make test         # deterministic unit and loopback transport tests
make integration  # installed artifact in real PostgreSQL 18 via Docker
```

CI runs the integration runner against PostgreSQL 14 through 18. Ordinary tests
use only synthetic loopback providers.

## Release

Released SQL files are immutable. Update `src/pg_prompt_jev.py`, add the next
base and upgrade targets in `tools/generate_sql.py`, run the generator, update
`default_version`, and test fresh install, upgrade, archive install, privileges,
and dump/restore. A successful tested push or merge to `main` creates a new tag,
GitHub release, and source archive only when that version and tag do not already
exist.

## License

[MIT](LICENSE)
