import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]


class GenerationTests(unittest.TestCase):
    def test_generated_sql_is_current(self):
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / "generate_sql.py"), "--check"],
            check=True,
        )

    def test_released_010_sql_is_immutable(self):
        contents = (ROOT / "sql" / "pg_prompt_jev--0.1.0.sql").read_bytes()
        digest = hashlib.sha1(
            f"blob {len(contents)}\0".encode() + contents
        ).hexdigest()
        self.assertEqual(digest, "956770c3f292041e56d3a18ab1e9b03009418064")


if __name__ == "__main__":
    unittest.main()
