"""Verify, scan, reconcile with the app, and clean confirmed deleted entries.

Preview by default. Use --apply from an external console, then close Codex/ChatGPT.
The workflow waits for the reopened desktop app to finish reconciliation before cleanup.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from clean_codex_catalog import Audit, connect, validate_schema
from clean_sidebar_references import candidates, KEY
import cleaner_language as ui
from runtime_paths import APP, ROOT, tool_command
from plan_sidebar_cleanup import load_reviewed_plan, archive_status


def choose_reference_plan(output: Path) -> Path | None:
    pending = output / "logs" / "pending-sidebar-reference-plan.json"
    if pending.is_file():
        print(ui.text("已有待執行的已核對清單: {path}", "A reviewed cleanup plan is ready: {path}", path=pending), flush=True)
        prompt = ui.text("Enter 使用這份清單, 或輸入其他 JSON 路徑, 輸入 CANCEL 取消: ",
                         "Press Enter to use it, enter another JSON path, or type CANCEL to cancel: ")
    else:
        prompt = ui.text("已核對的參照清理計畫 JSON 路徑 (空白取消): ",
                         "Reviewed sidebar reference plan JSON path (empty to cancel): ")
    answer = input(prompt).strip()
    if answer == "CANCEL" or (not answer and not pending.is_file()):
        return None
    if len(answer) >= 2 and answer[0] == answer[-1] == '"':
        answer = answer[1:-1]
    return (Path(answer).expanduser() if answer else pending).resolve(strict=True)


def inspect_sidebar_references(home: Path, audit: Audit) -> None:
    path = home / ".codex-global-state.json"
    if not path.exists():
        audit.record("sidebar_reference_inspection_unavailable", reason="No desktop sidebar state found.")
        return
    try:
        state = json.loads(path.read_bytes())
        accounts = state["electron-persisted-atom-state"].get(KEY, {})
        rows = [{"account_id": account, **row} for account in accounts for row in candidates(state, account)]
        report = {"read_only": True, "candidates": rows, "automatic_removals": [],
                  "cloud_deletion_verified": False}
        report_path = audit.directory / "sidebar-references.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        audit.record("sidebar_reference_inspection", candidate_count=len(rows), report=str(report_path))
        if rows:
            print(ui.text("專案快取中缺少 {count} 個側邊欄參照, 使用 --review-sidebar-references 核對, 缺少快取不是刪除證據", "{count} project links need review. Use --review-sidebar-references; missing cache does not prove deletion.", count=len(rows)), flush=True)
    except (KeyError, TypeError, ValueError) as error:
        audit.record("sidebar_reference_inspection_unavailable", reason=str(error), automatic_removals=0)
        print(ui.text("無法確認側邊欄資料格式, 不會自動移除參照", "Sidebar reference schema could not be verified; no references will be removed automatically."), flush=True)


def reconciliation_complete(home: Path) -> bool:
    with closing(connect(home / "sqlite" / "codex-dev.db", "ro")) as reader:
        validate_schema(reader)
        rows = reader.execute(
            "SELECT h.host_id,s.initial_build_complete,s.last_full_reconciled_at "
            "FROM local_thread_catalog_hosts h LEFT JOIN local_thread_catalog_sync_state s USING(host_id) "
            "WHERE h.host_kind='chatgpt'"
        ).fetchall()
        if len(rows) != 1 or rows[0][1] is None:
            raise RuntimeError("Expected exactly one ChatGPT host with sync state; stopping before cleanup.")
        host, complete, reconciled_at = rows[0]
        checkpoints = reader.execute(
            "SELECT checkpoint FROM local_thread_catalog_scan_checkpoints WHERE host_id=?", (host,)
        ).fetchall()
    if complete != 1 or not isinstance(reconciled_at, (int, float)) or reconciled_at <= 0:
        return False
    for (raw,) in checkpoints:
        try:
            if json.loads(raw)["attempt"]["mode"] != "incremental":
                return False
        except (TypeError, ValueError, KeyError):
            return False
    return True


def wait_for_reconciliation(home: Path, timeout: int, audit: Audit) -> None:
    audit.record("waiting_for_app_reconciliation", timeout_seconds=timeout)
    print(ui.text("請重開 Codex 並保留此外部視窗, 等待 App 完成清單核對再清理", "Reopen Codex and keep this terminal open. Waiting for app sync before cleanup."), flush=True)
    deadline = time.monotonic() + timeout
    while True:
        if reconciliation_complete(home):
            audit.record("app_reconciliation_observed", live_cloud_verified=False,
                         limitation="App sync-state completion only; not per-conversation verification across all dates.")
            print(ui.text("App 清單核對已完成, 正在檢查待清項目", "App sync finished. Checking for entries to clean."), flush=True)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("App reconciliation did not finish; cleanup was not run.")
        time.sleep(2)


def run_workflow(args: argparse.Namespace, audit: Audit) -> int:
    scripts = APP
    common = ["--codex-home", str(args.codex_home), "--output-dir", str(args.output_dir)]
    cleaner = tool_command(scripts / "clean_codex_catalog.py", *common)
    for root in args.log_root or []:
        cleaner.extend(["--log-root", str(root)])

    inspect_sidebar_references(args.codex_home, audit)
    reviewed = getattr(args, "reviewed_sidebar_plan", None)
    if reviewed is not None:
        with closing(connect(args.codex_home / "sqlite/codex-dev.db", "ro")) as reader:
            hosts = reader.execute("SELECT host_id FROM local_thread_catalog_hosts WHERE host_kind='chatgpt'").fetchall()
            current_ids = {row[0] for row in reader.execute(
                "SELECT thread_id FROM local_thread_catalog WHERE source_kind='chatgpt' AND missing_candidate=0")}
        if len(hosts) != 1:
            raise ValueError("Exactly one ChatGPT host is required for the reviewed cloud plan.")
        plan = load_reviewed_plan(reviewed, hosts[0][0])
        if {entry["thread_id"] for entry in plan["entries"]} & current_ids:
            raise ValueError("Reviewed old sidebar IDs overlap current active catalog entries; preview again.")
        snapshot_path = getattr(args, "archived_snapshot", None)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig")) if snapshot_path else None
        status = archive_status(plan, snapshot)
        (audit.directory / "cloud-cleanup-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        audit.record("cloud_sidebar_archive_status", verified_count=len(status["verified_ids"]),
                     pending_count=len(status["pending_ids"]), cloud_modified=False)
        if status["pending_ids"]:
            print(ui.text("雲端封存尚未完成, 請透過已登入的 Codex app 執行精確請求, 再用 --archived-snapshot 提供封存清單, 尚未要求本機重新核對", "Cloud archive actions are pending. Execute the exact requests through the signed-in Codex app, then supply --archived-snapshot from its archive listing. No local reconciliation was requested."), flush=True)
            return 2

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
        references = getattr(args, "sidebar_reference_plan", None)
        if references is not None or getattr(args, "review_sidebar_references", False):
            reference_command = tool_command(scripts / "clean_sidebar_references.py",
                                             *common, "--wait-seconds", str(args.wait_seconds))
            reference_command += ["--plan", str(references), "--apply"] if references else ["--interactive"]
            code = stage("clean_reviewed_sidebar_references", reference_command)
            if code:
                return code
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
        print(ui.text("已完成 App 清單核對與已確認刪除的索引清理, 其他側邊欄或載入錯誤可能仍存在, 請查看 load-diagnostics.json 並重開 Codex", "Sync and confirmed-deleted cache cleanup finished. Reopen Codex. If entries or loading errors remain, check load-diagnostics.json."), flush=True)
    else:
        print(ui.text("預覽完成, 使用 --apply 執行清單核對與清理", "Preview finished. Use --apply to reconcile and clean."), flush=True)
    return 0


def main() -> int:
    parser = ui.parser("檢查側邊欄, 核對舊參照, 重新同步並清除已確認刪除的索引", __doc__)
    parser.add_argument("--apply", action="store_true")
    reference = parser.add_mutually_exclusive_group()
    reference.add_argument("--review-sidebar-references", action="store_true",
                           help="Review missing cached project references before app reconciliation")
    reference.add_argument("--sidebar-reference-plan", type=Path, help="Exact reviewed sidebar reference plan")
    reference.add_argument("--interactive-sidebar-plan", action="store_true",
                           help="Select a reviewed reference plan from the external launcher")
    parser.add_argument("--reviewed-sidebar-plan", type=Path, help="Reviewed cloud archive request plan")
    parser.add_argument("--archived-snapshot", type=Path, help="Native Codex app archive listing snapshot for the reviewed IDs")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--log-root", type=Path, action="append")
    parser.add_argument("--wait-seconds", type=int, default=1800)
    parser.add_argument("--reconcile-wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    ui.configure(args)
    if args.wait_seconds < 0 or args.reconcile_wait_seconds < 0:
        parser.error("Wait timeouts cannot be negative")
    if args.archived_snapshot and not args.reviewed_sidebar_plan:
        parser.error("--archived-snapshot requires --reviewed-sidebar-plan")
    if args.interactive_sidebar_plan and not args.apply:
        parser.error("--interactive-sidebar-plan requires --apply")
    args.codex_home = args.codex_home.resolve()
    args.output_dir = args.output_dir.resolve()
    audit = Audit(args.output_dir / "logs" / ("sidebar-workflow-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    try:
        if args.interactive_sidebar_plan:
            args.sidebar_reference_plan = choose_reference_plan(args.output_dir)
            if args.sidebar_reference_plan is None:
                return 0
        return run_workflow(args, audit)
    except Exception:
        audit.record("workflow_failed", traceback=traceback.format_exc(),
                     limitation="Earlier successful stages remain committed; later stages were not run.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
