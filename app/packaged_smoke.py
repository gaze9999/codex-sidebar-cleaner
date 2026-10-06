"""Read-only packaged runtime smoke check, without opening browsers or Codex data."""
from contextlib import closing
import sqlite3


def main() -> int:
    with closing(sqlite3.connect(":memory:")) as db:
        assert db.execute("SELECT 1").fetchone()[0] == 1
    print("Packaged CLI runtime and SQLite: OK")
    return 0
