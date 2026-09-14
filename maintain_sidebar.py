"""Verify, scan, reconcile with the app, and clean confirmed deleted entries.

Preview by default. Use --apply from an external console, then close Codex/ChatGPT.
The workflow waits for the reopened desktop app to finish reconciliation before cleanup.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from clean_codex_catalog import Audit, connect, validate_schema


def reconciliation_complete(home: Path) -> bool:
    with closing(connect(home / "sqlite" / "codex-dev.db", "ro")) as reader:
        validate_schema(reader)
        rows = reader.execute(
            "SELECT s.initial_build_complete,s.last_full_reconciled_at, "
            "EXISTS(SELECT 1 FROM local_thread_catalog_scan_checkpoints p WHERE p.host_id=h.host_id) "
            "FROM local_thread_catalog_hosts h LEFT JOIN local_thread_catalog_sync_state s USING(host_id) "
            "WHERE h.host_kind='chatgpt'"
        ).fetchall()
    if len(rows) != 1 or rows[0][0] is None:
        raise RuntimeError("Expected exactly one ChatGPT host with sync state; stopping before cleanup.")
    complete, reconciled_at, pending = rows[0]
    return complete == 1 and isinstance(reconciled_at, (int, float)) and reconciled_at > 0 and not pending


def wait_for_reconciliation(home: Path, timeout: int, audit: Audit) -> None:
    audit.record("waiting_for_app_reconciliation", timeout_seconds=timeout)
    print("Reopen Codex now and keep THIS console open. Waiting for app reconciliation before cleanup.", flush=True)
    deadline = time.monotonic() + timeout
    while True:
        if reconciliation_complete(home):
            audit.record("app_reconciliation_observed", live_cloud_verified=False,
                         limitation="App sync-state completion only; not per-conversation verification across all dates.")
            print("App reconciliation finished. Exit Codex/ChatGPT again so cleanup can continue.", flush=True)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("App reconciliation did not finish; cleanup was not run.")
        time.sleep(2)


def run_workflow(args: argparse.Namespace, audit: Audit) -> int:
    scripts = Path(__file__).resolve().parent
    common = ["--codex-home", str(args.codex_home), "--output-dir", str(args.output_dir)]
    cleaner = [sys.executable, "-X", "utf8", "-u", str(scripts / "clean_codex_catalog.py"), *common]
    for root in args.log_root or []:
        cleaner.extend(["--log-root", str(root)])

    def stage(name: str, command: list[str]) -> int:
        audit.record("stage_started", stage=name, command=command)
        result = subprocess.run(command, check=False)
        audit.record("stage_finished", stage=name, exit_code=result.returncode)
        if result.returncode:
            audit.record("workflow_stopped", failed_stage=name,
                         limitation="Earlier successful stages remain committed. Inspect logs before retrying.")
        return result.returncode

    for name, flags in [("initial_verification", ["--verify"]), ("scan_all_dates", ["--scan-projects"])]:
        code = stage(name, [*cleaner, *flags])
        if code:
            return code
    if args.apply:
        code = stage("request_app_reconciliation", [*cleaner, "--apply", "--reconcile", "--wait-seconds", str(args.wait_seconds)])
        if code:
            return code
        wait_for_reconciliation(args.codex_home, args.reconcile_wait_seconds, audit)
        steps = [
            ("clean_confirmed_deleted", [*cleaner, "--apply", "--wait-seconds", str(args.wait_seconds)]),
        ]
    else:
        steps = []
    for name, command in steps:
        code = stage(name, command)
        if code:
            return code
    audit.record("workflow_completed", apply=args.apply,
                 app_reconciliation_pending=False, live_cloud_verified=False)
    if args.apply:
        print("App reconciliation and confirmed-deleted cleanup finished. Reopen Codex.", flush=True)
    else:
        print("Preview finished. Use --apply to reconcile and clean.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--log-root", type=Path, action="append")
    parser.add_argument("--wait-seconds", type=int, default=1800)
    parser.add_argument("--reconcile-wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.wait_seconds < 0 or args.reconcile_wait_seconds < 0:
        parser.error("Wait timeouts cannot be negative")
    args.codex_home = args.codex_home.resolve()
    args.output_dir = args.output_dir.resolve()
    audit = Audit(args.output_dir / "logs" / ("sidebar-workflow-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    try:
        return run_workflow(args, audit)
    except Exception:
        audit.record("workflow_failed", traceback=traceback.format_exc(),
                     limitation="Earlier successful stages remain committed; later stages were not run.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
