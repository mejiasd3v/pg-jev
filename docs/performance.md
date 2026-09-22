# Performance

Run the default PostgreSQL loopback benchmark:

```sh
make benchmark
```

It records cold and warm calls, 1 and 16 concurrent sessions, a 1 KiB input,
one question, and a controlled 10 ms provider delay. Use `python3 bench/run.py
--full` for the 1/16/64 session, 1/8/64 KiB, and 1/8/64 question matrix. The
full Cartesian matrix is intentionally not a pull-request gate.

Each artifact records PostgreSQL, Python, operating system, CPU count, commit,
scenario parameters, p50/p95/p99 and mean latency, throughput, errors, request
count, accepted TCP connections, and backend RSS after the run. Warmup-sensitive
cold and warm cases are separate. Real provider calls are never made.

The current harness does not measure TLS handshakes or isolate normalization-only
time. Those gates remain open rather than being inferred from loopback HTTP.
Provider latency, production throughput, memory peaks, and cancellation behavior
must not be extrapolated from these results.

`bench/results/baseline.json` is the pre-connection-reuse baseline.
`bench/results/connection-reuse.json` repeats the same default scenarios after
T09.

In the recorded PostgreSQL 18.6 loopback runs, ten warm calls changed from ten
to one accepted TCP connection, and 80 calls across 16 sessions changed from 80
to 16 connections. Warm single-session p50 latency regressed from 5.633 ms to
44.075 ms; the controlled-delay p50 regressed from 16.901 ms to 55.074 ms. This
fixture exhibits a persistent-connection latency penalty, so no latency or
throughput improvement is claimed. Connection reuse is retained only for the
measured reduction in connection setup; deployments should benchmark their TLS
provider or gateway before relying on it.

A percentage performance claim requires repeated runs and observed dispersion.
These artifacts are measurements from one local environment, not guarantees.
