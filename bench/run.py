#!/usr/bin/env python3
import argparse
import glob
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]


def percentile(values, percent):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percent))]


def host_run(image, full, output):
    command = [
        "docker", "run", "--rm",
        "-e", "POSTGRES_PASSWORD=test",
        "-e", "BENCH_COMMIT=" + subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "-v", f"{ROOT}:/src",
        image,
        "sh", "-euxc",
        'apt-get update -qq; '
        'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq make procps python3 '
        '"postgresql-plpython3-$PG_MAJOR" "postgresql-server-dev-$PG_MAJOR" >/dev/null; '
        f'python3 /src/bench/run.py --inside {"--full" if full else ""} '
        f'--output /src/{output}',
    ]
    subprocess.run(command, check=True)


def inside_run(full, output):
    sys.path.insert(0, "/src")
    from test.http_fixture import HttpFixture, Response

    delay = {"seconds": 0.0}

    def answer(request):
        if delay["seconds"]:
            time.sleep(delay["seconds"])
        body = json.loads(request["body"])
        questions = body["questions"]
        return Response.json({
            "model": "jev-bench",
            "answers": {
                name: {"type": "noul", "noul": 0.5}
                for name in questions
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    subprocess.run(["make", "-C", "/src", "install"], check=True)
    with HttpFixture(answer) as fixture:
        environment = {
            **os.environ,
            "PROMPT_JEV_PROVIDER": "typesafe",
            "TYPESAFE_API_KEY": "benchmark-key",
            "TYPESAFE_BASE_URL": fixture.url,
            "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
        }
        server = subprocess.Popen(
            ["docker-entrypoint.sh", "postgres"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        logs = []
        ready = threading.Event()

        def collect_logs():
            for line in server.stdout:
                logs.append(line)
                if "PostgreSQL init process complete" in line:
                    ready.set()

        thread = threading.Thread(target=collect_logs, daemon=True)
        thread.start()
        try:
            if not ready.wait(30):
                raise RuntimeError("".join(logs))
            while subprocess.run(
                ["pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                stdout=subprocess.DEVNULL,
            ).returncode:
                time.sleep(0.1)
            pg_environment = {**os.environ, "PGPASSWORD": "test"}
            subprocess.run(
                ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-U", "postgres", "-c",
                 "CREATE EXTENSION plpython3u; CREATE EXTENSION pg_prompt_jev; "
                 "GRANT EXECUTE ON FUNCTION prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb) TO PUBLIC;"],
                check=True,
                env=pg_environment,
                stdout=subprocess.DEVNULL,
            )

            scenarios = [
                {"name": "cold-c1-1k-1q", "clients": 1, "transactions": 1, "input_kib": 1, "questions": 1, "delay": 0},
                {"name": "warm-c1-1k-1q", "clients": 1, "transactions": 10, "input_kib": 1, "questions": 1, "delay": 0},
                {"name": "warm-c16-1k-1q", "clients": 16, "transactions": 5, "input_kib": 1, "questions": 1, "delay": 0},
                {"name": "delay-c1-1k-1q", "clients": 1, "transactions": 5, "input_kib": 1, "questions": 1, "delay": 0.01},
            ]
            if full:
                scenarios = [
                    {
                        "name": f"c{clients}-{size}k-{questions}q",
                        "clients": clients,
                        "transactions": 2,
                        "input_kib": size,
                        "questions": questions,
                        "delay": 0,
                    }
                    for clients in (1, 16, 64)
                    for size in (1, 8, 64)
                    for questions in (1, 8, 64)
                ]

            results = []
            for scenario in scenarios:
                delay["seconds"] = scenario["delay"]
                payload = "x" * (scenario["input_kib"] * 1024)
                if scenario["questions"] == 1:
                    call = "prompt_jev(%s, 'Question?')"
                    arguments = [payload]
                else:
                    questions = {
                        f"q{index}": {"type": "noul", "instructions": "Question?"}
                        for index in range(scenario["questions"])
                    }
                    call = "prompt_jev(%s, questions => %s::jsonb)"
                    arguments = [payload, json.dumps(questions, separators=(",", ":"))]
                sql = "SELECT " + call + ";\n"
                for argument in arguments:
                    sql = sql.replace("%s", "'" + argument.replace("'", "''") + "'", 1)
                with tempfile.NamedTemporaryFile("w", suffix=".sql") as script:
                    script.write(sql)
                    script.flush()
                    prefix = f"/tmp/pgbench-{scenario['name']}"
                    before_requests = len(fixture.requests)
                    before_connections = fixture.connection_count
                    started = time.monotonic()
                    completed = subprocess.run([
                        "pgbench", "-n", "-h", "127.0.0.1", "-U", "postgres",
                        "-c", str(scenario["clients"]),
                        "-j", str(min(scenario["clients"], 8)),
                        "-t", str(scenario["transactions"]),
                        "-f", script.name,
                        "-l", f"--log-prefix={prefix}", "postgres",
                    ], text=True, capture_output=True, env=pg_environment)
                    elapsed = time.monotonic() - started
                latencies = []
                for log in glob.glob(prefix + "*"):
                    for line in Path(log).read_text().splitlines():
                        fields = line.split()
                        if len(fields) >= 3 and fields[2].isdigit():
                            latencies.append(int(fields[2]) / 1000)
                transactions = scenario["clients"] * scenario["transactions"]
                results.append({
                    **scenario,
                    "transactions": transactions,
                    "elapsed_seconds": elapsed,
                    "throughput_tps": transactions / elapsed,
                    "errors": 0 if completed.returncode == 0 else 1,
                    "requests": len(fixture.requests) - before_requests,
                    "tcp_connections": fixture.connection_count - before_connections,
                    "latency_ms": {
                        "p50": percentile(latencies, 0.50),
                        "p95": percentile(latencies, 0.95),
                        "p99": percentile(latencies, 0.99),
                        "mean": statistics.mean(latencies),
                    } if latencies else None,
                    "pgbench_output": completed.stdout + completed.stderr,
                })
                if completed.returncode:
                    raise RuntimeError(completed.stdout + completed.stderr)

            delay["seconds"] = 0
            requests_before = len(fixture.requests)
            scalar_output = subprocess.check_output(
                ["psql", "-XAt", "-h", "127.0.0.1", "-U", "postgres", "-c",
                 "SELECT prompt_jev('same input', 'One?'); "
                 "SELECT prompt_jev('same input', 'Two?'); "
                 "SELECT prompt_jev('same input', 'Three?');"],
                text=True,
                env=pg_environment,
            ).strip().splitlines()
            scalar_requests = len(fixture.requests) - requests_before
            requests_before = len(fixture.requests)
            multi_output = json.loads(subprocess.check_output(
                ["psql", "-XAt", "-h", "127.0.0.1", "-U", "postgres", "-c",
                 "SELECT prompt_jev('same input', questions => '{"
                 "\"one\":{\"type\":\"noul\",\"instructions\":\"One?\"},"
                 "\"two\":{\"type\":\"noul\",\"instructions\":\"Two?\"},"
                 "\"three\":{\"type\":\"noul\",\"instructions\":\"Three?\"}"
                 "}'::jsonb)::text;"],
                text=True,
                env=pg_environment,
            ).strip())
            comparison = {
                "scalar_requests": scalar_requests,
                "multi_question_requests": len(fixture.requests) - requests_before,
                "equivalent": scalar_output == ["0.5", "0.5", "0.5"]
                and set(multi_output.values()) == {0.5},
            }

            postgres_version = subprocess.check_output(
                ["psql", "-XAt", "-h", "127.0.0.1", "-U", "postgres", "-c", "SHOW server_version"],
                text=True,
                env=pg_environment,
            ).strip()
            rss_kib = sum(
                int(value) for value in subprocess.check_output(
                    ["ps", "-o", "rss=", "-C", "postgres"], text=True
                ).split()
            )
            artifact = {
                "commit": os.environ.get("BENCH_COMMIT", "unknown"),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "environment": {
                    "postgres": postgres_version,
                    "python": platform.python_version(),
                    "os": platform.platform(),
                    "cpu_count": os.cpu_count(),
                    "backend_rss_kib_after": rss_kib,
                    "tls": "not measured",
                    "normalization_only": "not measured independently",
                },
                "scenarios": results,
                "scalar_vs_multi_question": comparison,
            }
            target = Path(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(artifact, indent=2) + "\n")
            print(json.dumps(artifact, indent=2))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
            thread.join(timeout=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--image", default="postgres:18")
    parser.add_argument("--output", default="bench/results/latest.json")
    args = parser.parse_args()
    if args.inside:
        inside_run(args.full, args.output)
    else:
        host_run(args.image, args.full, args.output)


if __name__ == "__main__":
    main()
