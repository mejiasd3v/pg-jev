# Changelog

## 0.1.1 (unreleased)

### Confirmed fixes

- Redirects are rejected without forwarding authorization.
- Provider error bodies are redacted from database errors and logs.
- Inputs and provider answers are validated before use; missing probabilities
  are no longer fabricated.
- Requests, responses, question counts, and JSON nesting are bounded.
- Fresh installs and upgrades preserve the six-argument function contract,
  explicit grants, dependencies, and public execution revocation.

### Compatibility tightening

- Object-form Score criteria are rejected because JSON object order cannot
  represent the ordered score contract.
- Unknown question fields, malformed distributions, incomplete answers, and
  provider responses without documented model and usage metadata are rejected.
- Vercel has its own documented default model. The previous
  `TYPESAFE_DEFAULT_MODEL` setting remains a backward-compatible fallback.

### Measured limitations

- PostgreSQL 18.6 loopback tests preserved cancellation SQLSTATE `57014` and
  backend usability, but a stalled header read observed cancellation only after
  the 2 second connection-phase timeout.
- No live provider compatibility, production throughput, TLS performance, or
  cross-platform cancellation guarantee has been established.
- Connection reuse reduced accepted loopback TCP connections but regressed
  loopback latency. No latency or throughput improvement is claimed; see the
  raw benchmark artifacts and `docs/performance.md`.
