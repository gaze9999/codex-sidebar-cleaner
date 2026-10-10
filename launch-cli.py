"""Shared source and packaged CLI entry point, limited to bundled tools."""
from contextlib import closing
from pathlib import Path
import runpy
import sqlite3
import sys


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None:
            # Match the source launchers' UTF-8 mode, including redirected CLI output.
            options = {"encoding": "utf-8"}
            if stream is not sys.stdin:
                options.update(line_buffering=True, write_through=True)
            stream.reconfigure(**options)
    source = Path(sys._MEIPASS) / "app" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "app"
    sys.path.insert(0, str(source))
    if sys.argv[1:2] == ["--self-test"]:
        with closing(sqlite3.connect(":memory:")) as db:
            assert db.execute("SELECT 1").fetchone()[0] == 1
        print("Packaged CLI runtime and SQLite: OK")
        return 0
    tools = {"cli.py", "clean_archived_conversations.py", "clean_codex_catalog.py",
             "clean_sidebar_references.py", "compare_cloud_catalog.py", "delete_archived_threads.py",
             "maintain_sidebar.py", "organize_local_threads.py", "plan_sidebar_cleanup.py"}
    if sys.argv[1:2] == ["--tool"]:
        if len(sys.argv) < 3 or sys.argv[2] not in tools:
            raise SystemExit("Unknown bundled tool")
        script = source / sys.argv[2]
        sys.argv = [str(script), *sys.argv[3:]]
        runpy.run_path(str(script), run_name="__main__")
        return 0
    if sys.argv[1:2] == ["--cli"]:
        sys.argv = [sys.argv[0], *sys.argv[2:]]
    from cli import main as launch
    return launch()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
