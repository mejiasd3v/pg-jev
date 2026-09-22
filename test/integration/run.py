#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parents[2]


def run_container(image):
    command = [
        "docker", "run", "--rm",
        "-e", "POSTGRES_PASSWORD=test",
        "-v", f"{ROOT}:/src:ro",
        image,
        "sh", "-euxc",
        'apt-get update -qq; '
        'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq make python3 '
        '"postgresql-plpython3-$PG_MAJOR" "postgresql-server-dev-$PG_MAJOR" >/dev/null; '
        'python3 /src/test/integration/run.py --inside',
    ]
    subprocess.run(command, check=True)


def run_inside():
    sys.path.insert(0, "/src")
    from test.http_fixture import HttpFixture, Response

    def answer(request):
        body = json.loads(request["body"])
        if body["state"] == "redirect":
            return Response(
                status=302,
                body=b"synthetic-secret private prompt",
                headers={"Location": fixture.url + "/target"},
            )
        if body["state"] == "malformed":
            return Response(body=b"<html>not json</html>")
        if body["state"] == "stall":
            return Response(body=b"{}", header_delay=5)
        answers = {}
        for name, question in body["questions"].items():
            if question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.75}
            elif question["type"] == "choice":
                labels = list(question["criteria"])
                answers[name] = {
                    "type": "choice",
                    "choice": labels[0],
                    "probabilities": {
                        label: 1.0 if index == 0 else 0.0
                        for index, label in enumerate(labels)
                    },
                    "confidence": 1.0,
                }
            else:
                labels = question["criteria"]
                answers[name] = {
                    "type": "score",
                    "score": 1.0,
                    "legend": {
                        str(index): str(label) for index, label in enumerate(labels)
                    },
                    "probabilities": {
                        str(index): 1.0 if index == 1 else 0.0
                        for index in range(len(labels))
                    },
                    "confidence": 1.0,
                }
        return Response.json({
            "model": "jev-test",
            "answers": answers,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    subprocess.run(["make", "-C", "/src", "install"], check=True)
    archive = "/tmp/pg-jev-0.1.1.tar.gz"
    package_files = [
        ".github", "CHANGELOG.md", "LICENSE", "Makefile", "README.md",
        "SECURITY.md", "bench", "docs", "pg_prompt_jev.control", "sql", "src",
        "test", "tools",
    ]
    subprocess.run([
        "tar", "-C", "/src", "-czf", archive,
        "--transform=s,^,pg-jev-0.1.1/,",        *package_files,
    ], check=True)
    subprocess.run(["mkdir", "-p", "/tmp/package"], check=True)
    subprocess.run(["tar", "-xzf", archive, "-C", "/tmp/package"], check=True)
    subprocess.run(
        ["make", "-C", "/tmp/package/pg-jev-0.1.1", "install"], check=True
    )

    with HttpFixture(answer) as fixture:
        environment = {
            **os.environ,
            "PROMPT_JEV_PROVIDER": "typesafe",
            "TYPESAFE_API_KEY": "synthetic-test-key",
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
        initialized = threading.Event()

        def collect_logs():
            for line in server.stdout:
                logs.append(line)
                if "PostgreSQL init process complete" in line:
                    initialized.set()

        log_thread = threading.Thread(target=collect_logs, daemon=True)
        log_thread.start()
        try:
            if not initialized.wait(30):
                raise RuntimeError("PostgreSQL initialization failed:\n" + "".join(logs))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                ready = subprocess.run(
                    ["pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if ready.returncode == 0:
                    break
                if server.poll() is not None:
                    raise RuntimeError("".join(logs))
                time.sleep(0.2)
            else:
                raise RuntimeError("PostgreSQL did not become ready")

            sql = """
                CREATE EXTENSION plpython3u;
                CREATE EXTENSION pg_prompt_jev;
                DO $do$
                DECLARE value jsonb;
                BEGIN
                  value := prompt_jev('hello', 'Is this a greeting?');
                  IF value <> '0.75'::jsonb THEN
                    RAISE EXCEPTION 'noul output shape changed';
                  END IF;

                  value := prompt_jev(
                    'severity', 'Rate it', score => '["low", "medium", "high"]'::jsonb
                  );
                  IF value->'probabilities'->0->>'value' <> 'low'
                     OR value->'probabilities'->2->>'value' <> 'high' THEN
                    RAISE EXCEPTION 'score output order changed';
                  END IF;

                  value := prompt_jev(
                    'route', 'Pick one', choice => '["billing", "technical"]'::jsonb
                  );
                  IF value <> '{
                    "choice": "billing",
                    "probabilities": [
                      {"value": "billing", "probability": 1.0},
                      {"value": "technical", "probability": 0.0}
                    ],
                    "confidence": 1.0
                  }'::jsonb THEN
                    RAISE EXCEPTION 'choice output shape changed';
                  END IF;

                  value := prompt_jev(
                    'both', questions => '{
                      "yes": {"type": "noul", "instructions": "Yes?"},
                      "team": {
                        "type": "choice",
                        "instructions": "Team?",
                        "criteria": ["billing", "technical"]
                      }
                    }'::jsonb
                  );
                  IF value <> '{
                    "yes": 0.75,
                    "team": {
                      "choice": "billing",
                      "probabilities": [
                        {"value": "billing", "probability": 1.0},
                        {"value": "technical", "probability": 0.0}
                      ],
                      "confidence": 1.0
                    }
                  }'::jsonb THEN
                    RAISE EXCEPTION 'multi-question output shape changed';
                  END IF;
                END
                $do$;
                DO $do$
                BEGIN
                  PERFORM prompt_jev('redirect', 'Follow it?');
                  RAISE EXCEPTION 'expected redirect rejection';
                EXCEPTION WHEN OTHERS THEN
                  IF SQLERRM NOT LIKE '%status=302%'
                     OR SQLERRM LIKE '%synthetic-secret%'
                     OR SQLERRM LIKE '%private prompt%' THEN
                    RAISE;
                  END IF;
                END
                $do$;
                DO $do$
                BEGIN
                  PERFORM prompt_jev('malformed', 'Parse it?');
                  RAISE EXCEPTION 'expected invalid response';
                EXCEPTION WHEN SQLSTATE 'JV006' THEN
                  NULL;
                END
                $do$;
                DO $do$
                BEGIN
                  PERFORM prompt_jev(
                    'invalid', 'Rate it', score => '{"low": null, "high": null}'::jsonb
                  );
                  RAISE EXCEPTION 'expected score object rejection';
                EXCEPTION WHEN SQLSTATE '22023' THEN
                  IF SQLERRM NOT LIKE '%ordered JSON array%' THEN
                    RAISE;
                  END IF;
                END
                $do$;
                SELECT 'integration-ok';
            """
            output = subprocess.check_output(
                ["psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-U", "postgres", "-c", sql],
                text=True,
                env={**os.environ, "PGPASSWORD": "test"},
            ).strip().splitlines()
            if "integration-ok" not in output:
                raise AssertionError(f"SQL assertions did not complete: {output}")
            psql = [
                "psql", "-XAt", "-h", "127.0.0.1", "-U", "postgres"
            ]
            psql_environment = {**os.environ, "PGPASSWORD": "test"}

            requests_before_reuse = len(fixture.requests)
            connections_before_reuse = fixture.connection_count
            subprocess.check_call(
                psql + ["-v", "ON_ERROR_STOP=1", "-c", """
                    SELECT prompt_jev('reuse', 'Same context?')
                    FROM generate_series(1, 5);
                """],
                env=psql_environment,
                stdout=subprocess.DEVNULL,
            )
            if (
                len(fixture.requests) != requests_before_reuse + 5
                or fixture.connection_count != connections_before_reuse + 1
            ):
                raise AssertionError("same-backend calls did not reuse one connection")

            attributes = subprocess.check_output(
                psql + ["-c", """
                    SELECT pronargs = 6
                       AND prorettype = 'jsonb'::regtype
                       AND provolatile = 'v'
                       AND proparallel = 'u'
                       AND NOT prosecdef
                       AND NOT proisstrict
                    FROM pg_proc
                    WHERE oid = 'prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure;
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t" not in attributes:
                raise AssertionError(f"function contract changed: {attributes}")

            requests_before_privileges = len(fixture.requests)
            connections_before_privileges = fixture.connection_count
            privilege_output = subprocess.check_output(
                psql + ["-v", "ON_ERROR_STOP=1", "-c", """
                    CREATE ROLE app_user;
                    CREATE SCHEMA attacker AUTHORIZATION app_user;
                    SET ROLE app_user;
                    CREATE FUNCTION attacker.current_setting(text, boolean)
                    RETURNS text LANGUAGE sql IMMUTABLE AS $$SELECT 'vercel'$$;
                    SET search_path = attacker, public, pg_catalog;
                    DO $do$
                    BEGIN
                      PERFORM prompt_jev('denied', 'No request?');
                      RAISE EXCEPTION 'expected permission denial';
                    EXCEPTION WHEN insufficient_privilege THEN
                      NULL;
                    END
                    $do$;
                    RESET ROLE;
                    GRANT EXECUTE ON FUNCTION prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)
                    TO app_user;
                    SET ROLE app_user;
                    SET search_path = attacker, public, pg_catalog;
                    SET jev.provider = 'vercel';
                    SELECT prompt_jev('granted', 'One request?')::text;
                    RESET ROLE;
                    SELECT prompt_jev('superuser', 'New role context?')::text;
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "0.75" not in privilege_output:
                raise AssertionError(f"granted role call failed: {privilege_output}")
            if (
                len(fixture.requests) != requests_before_privileges + 2
                or fixture.connection_count != connections_before_privileges + 2
            ):
                raise AssertionError("permission or role-context request counts are incorrect")

            local_setting = subprocess.check_output(
                psql + ["-c", """
                    BEGIN;
                    SET LOCAL jev.total_timeout = '1';
                    ROLLBACK;
                    SELECT COALESCE(
                      pg_catalog.current_setting('jev.total_timeout', true), ''
                    ) <> '1';
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t" not in local_setting:
                raise AssertionError(f"SET LOCAL leaked after rollback: {local_setting}")

            requests_before_rollback = len(fixture.requests)
            subprocess.check_call(
                psql + ["-v", "ON_ERROR_STOP=1", "-c", """
                    BEGIN;
                    SELECT prompt_jev('external side effect', 'Sent?');
                    ROLLBACK;
                """],
                env=psql_environment,
                stdout=subprocess.DEVNULL,
            )
            if len(fixture.requests) != requests_before_rollback + 1:
                raise AssertionError("transaction rollback unexpectedly removed the HTTP request")

            requests_before_bounded_example = len(fixture.requests)
            bounded_example = subprocess.check_output(
                psql + ["-v", "ON_ERROR_STOP=1", "-c", """
                    CREATE TEMP TABLE support_messages(
                      id integer, message text, evaluation jsonb
                    );
                    INSERT INTO support_messages VALUES
                      (1, 'refund one', NULL),
                      (2, 'refund two', NULL),
                      (3, 'refund three', NULL);
                    WITH candidates AS MATERIALIZED (
                      SELECT id, message
                      FROM support_messages
                      WHERE evaluation IS NULL
                      ORDER BY id
                      LIMIT 2
                    ), evaluated AS MATERIALIZED (
                      SELECT id, prompt_jev(
                        message,
                        questions => '{
                          "refund": {
                            "type": "noul",
                            "instructions": "Does this request a refund?"
                          },
                          "team": {
                            "type": "choice",
                            "instructions": "Which team owns this?",
                            "criteria": ["billing", "technical", "sales"]
                          }
                        }'::jsonb
                      ) AS result
                      FROM candidates
                    )
                    SELECT count(result) = 2 FROM evaluated;
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t" not in bounded_example:
                raise AssertionError(f"bounded operations example failed: {bounded_example}")
            if len(fixture.requests) != requests_before_bounded_example + 2:
                raise AssertionError("bounded operations example made the wrong request count")

            subprocess.check_call(
                psql + ["-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE pg_jev_schema_test"],
                env=psql_environment,
                stdout=subprocess.DEVNULL,
            )
            schema_psql = psql + ["-d", "pg_jev_schema_test"]
            schema_output = subprocess.check_output(
                schema_psql + ["-v", "ON_ERROR_STOP=1", "-c", """
                    CREATE SCHEMA private;
                    CREATE EXTENSION plpython3u;
                    CREATE EXTENSION pg_prompt_jev WITH SCHEMA private;
                    GRANT USAGE ON SCHEMA private TO app_user;
                    SELECT p.pronamespace = 'private'::regnamespace
                       AND p.provolatile = 'v'
                       AND p.proparallel = 'u'
                       AND NOT p.prosecdef
                       AND NOT p.proisstrict
                    FROM pg_proc p
                    WHERE p.oid = 'private.prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure;
                    SET ROLE app_user;
                    DO $do$
                    BEGIN
                      PERFORM private.prompt_jev('denied', 'No request?');
                      RAISE EXCEPTION 'expected permission denial';
                    EXCEPTION WHEN insufficient_privilege THEN
                      NULL;
                    END
                    $do$;
                    RESET ROLE;
                    SELECT p.pronamespace = 'private'::regnamespace
                       AND p.provolatile = 'v'
                       AND p.proparallel = 'u'
                       AND NOT p.prosecdef
                       AND NOT p.proisstrict
                    FROM pg_proc p
                    WHERE p.oid =
                      'private.prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure;
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t" not in schema_output:
                raise AssertionError(f"non-public schema install failed: {schema_output}")

            subprocess.check_call(
                psql + ["-v", "ON_ERROR_STOP=1", "-c", "CREATE ROLE upgrade_user"],
                env=psql_environment,
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(
                psql + ["-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE pg_jev_upgrade_test"],
                env=psql_environment,
                stdout=subprocess.DEVNULL,
            )
            upgrade_psql = psql + ["-d", "pg_jev_upgrade_test"]
            upgrade_output = subprocess.check_output(
                upgrade_psql + ["-v", "ON_ERROR_STOP=1", "-c", """
                    CREATE EXTENSION plpython3u;
                    CREATE EXTENSION pg_prompt_jev VERSION '0.1.0';
                    CREATE VIEW dependent_view AS
                      SELECT prompt_jev(NULL, 'unused') AS result;
                    GRANT EXECUTE ON FUNCTION
                      prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)
                    TO upgrade_user;
                    CREATE TEMP TABLE original_function AS
                      SELECT oid FROM pg_proc
                      WHERE oid = 'prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure;
                    ALTER EXTENSION pg_prompt_jev UPDATE TO '0.1.1';
                    SELECT e.extversion = '0.1.1'
                       AND p.oid = o.oid
                       AND to_regclass('dependent_view') IS NOT NULL
                       AND has_function_privilege(
                         'upgrade_user', p.oid, 'EXECUTE'
                       )
                       AND NOT EXISTS (
                         SELECT 1
                         FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
                         WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                       )
                       AND p.provolatile = 'v'
                       AND p.proparallel = 'u'
                       AND NOT p.prosecdef
                       AND NOT p.proisstrict
                    FROM pg_extension e
                    JOIN pg_proc p ON p.oid =
                      'prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure
                    CROSS JOIN original_function o
                    WHERE e.extname = 'pg_prompt_jev';
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t" not in upgrade_output:
                raise AssertionError(f"extension upgrade contract failed: {upgrade_output}")

            fresh_definition = subprocess.check_output(
                psql + ["-c", """
                    SELECT md5(pg_get_functiondef(
                      'prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure
                    ));
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()[-1]
            upgraded_definition = subprocess.check_output(
                upgrade_psql + ["-c", """
                    SELECT md5(pg_get_functiondef(
                      'prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure
                    ));
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()[-1]
            if fresh_definition != upgraded_definition:
                raise AssertionError("fresh and upgraded function definitions differ")

            subprocess.check_call(
                [
                    "pg_dump", "-h", "127.0.0.1", "-U", "postgres",
                    "-Fc", "-f", "/tmp/pg_jev.dump", "pg_jev_upgrade_test",
                ],
                env=psql_environment,
            )
            subprocess.check_call(
                psql + ["-c", "CREATE DATABASE pg_jev_restore_test"],
                env=psql_environment,
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(
                [
                    "pg_restore", "-h", "127.0.0.1", "-U", "postgres",
                    "-d", "pg_jev_restore_test", "/tmp/pg_jev.dump",
                ],
                env=psql_environment,
            )
            restore_output = subprocess.check_output(
                psql + ["-d", "pg_jev_restore_test", "-c", """
                    SELECT to_regclass('dependent_view') IS NOT NULL,
                           has_function_privilege(
                             'upgrade_user',
                             'prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)',
                             'EXECUTE'
                           ),
                           (SELECT extversion = '0.1.1' FROM pg_extension
                            WHERE extname = 'pg_prompt_jev');
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t|t|t" not in restore_output:
                raise AssertionError(f"dump/restore contract failed: {restore_output}")

            relocation = subprocess.check_output(
                upgrade_psql + ["-v", "ON_ERROR_STOP=1", "-c", """
                    CREATE SCHEMA moved;
                    ALTER EXTENSION pg_prompt_jev SET SCHEMA moved;
                    SELECT p.pronamespace = 'moved'::regnamespace
                    FROM pg_proc p
                    WHERE p.oid =
                      'moved.prompt_jev(text,text,jsonb,jsonb,jsonb,jsonb)'::regprocedure;
                """],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t" not in relocation:
                raise AssertionError(f"extension relocation failed: {relocation}")

            started = time.monotonic()
            timed = subprocess.run(
                psql,
                input="""
                    \\set VERBOSITY verbose
                    \\set ON_ERROR_STOP off
                    SET statement_timeout = '200ms';
                    SELECT prompt_jev('stall', 'Wait?');
                    SELECT 'statement-timeout-backend-usable';
                """,
                text=True,
                capture_output=True,
                env=psql_environment,
                timeout=10,
            )
            statement_timeout_elapsed = time.monotonic() - started
            statement_timeout_cancelled = "57014" in timed.stderr
            if (
                "statement-timeout-backend-usable" not in timed.stdout
                or not (statement_timeout_cancelled or "JV002" in timed.stderr)
            ):
                raise AssertionError(
                    "statement_timeout experiment did not fail safely or preserve backend usability:\n"
                    + timed.stdout + timed.stderr
                )

            cancelled = subprocess.Popen(
                psql,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=psql_environment,
            )
            cancelled.stdin.write("\\set VERBOSITY verbose\nSELECT pg_backend_pid();\n")
            cancelled.stdin.flush()
            backend_pid = int(cancelled.stdout.readline().strip())
            requests_before_cancel = len(fixture.requests)
            cancelled.stdin.write("SELECT prompt_jev('stall', 'Wait?');\n")
            cancelled.stdin.flush()
            deadline = time.monotonic() + 5
            while len(fixture.requests) == requests_before_cancel and time.monotonic() < deadline:
                time.sleep(0.01)
            if len(fixture.requests) == requests_before_cancel:
                raise AssertionError("cancel test request never reached the fixture")
            cancel_started = time.monotonic()
            cancel_result = subprocess.check_output(
                psql + ["-c", f"SELECT pg_cancel_backend({backend_pid});"],
                text=True,
                env=psql_environment,
            ).strip().splitlines()
            if "t" not in cancel_result:
                raise AssertionError(f"pg_cancel_backend failed: {cancel_result}")
            cancelled.stdin.write("SELECT 'cancel-backend-usable';\n\\q\n")
            cancelled.stdin.flush()
            stdout, stderr = cancelled.communicate(timeout=10)
            cancel_elapsed = time.monotonic() - cancel_started
            backend_cancelled = "57014" in stderr
            if (
                "cancel-backend-usable" not in stdout
                or not (backend_cancelled or "JV002" in stderr)
            ):
                raise AssertionError(
                    "pg_cancel_backend experiment did not fail safely or preserve backend usability:\n"
                    + stdout + stderr
                )

            if len(fixture.requests) != 18:
                raise AssertionError(f"expected eighteen HTTP requests, got {len(fixture.requests)}")
            if any(request["path"] == "/target" for request in fixture.requests):
                raise AssertionError("redirect target received a request")
            server_log = "".join(logs)
            if "synthetic-secret" in server_log or "private prompt" in server_log:
                raise AssertionError("sensitive provider error appeared in PostgreSQL logs")
            print(
                "PostgreSQL integration: ok "
                f"(statement_timeout={'57014' if statement_timeout_cancelled else 'JV002'} "
                f"after {statement_timeout_elapsed:.3f}s; "
                f"pg_cancel_backend={'57014' if backend_cancelled else 'JV002'} "
                f"after {cancel_elapsed:.3f}s)"
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            log_thread.join(timeout=1)
            if server.returncode not in (0, -15):
                print("".join(logs), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--image", default="postgres:18")
    args = parser.parse_args()
    if args.inside:
        run_inside()
    else:
        run_container(args.image)


if __name__ == "__main__":
    main()
