#!/usr/bin/env python3
import argparse
import difflib
from pathlib import Path

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "pg_prompt_jev.py"
SIGNATURE = "prompt_jev(text, text, jsonb, jsonb, jsonb, jsonb)"
HEADER = '''{create} prompt_jev(
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
'''
COMMENT = f'''COMMENT ON FUNCTION {SIGNATURE} IS
'Send text and closed questions to a configured Jev provider; grant EXECUTE only to roles allowed to export that data.';
'''


def render(create, revoke_public=True):
    revoke = f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC;\n\n" if revoke_public else ""
    return HEADER.format(create=create) + SOURCE.read_text() + "$PY$;\n\n" + revoke + COMMENT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = {
        ROOT / "sql" / "pg_prompt_jev--0.1.1.sql": render("CREATE FUNCTION"),
        ROOT / "sql" / "pg_prompt_jev--0.1.0--0.1.1.sql": render(
            "CREATE OR REPLACE FUNCTION", revoke_public=False
        ),
    }
    stale = False
    for path, expected in outputs.items():
        actual = path.read_text() if path.exists() else ""
        if actual == expected:
            continue
        stale = True
        if args.check:
            print("".join(difflib.unified_diff(
                actual.splitlines(True), expected.splitlines(True),
                fromfile=str(path), tofile=f"generated:{path}",
            )), end="")
        else:
            path.write_text(expected)
            print(path.relative_to(ROOT))
    if args.check and stale:
        raise SystemExit("generated SQL is stale; run python3 tools/generate_sql.py")


if __name__ == "__main__":
    main()
