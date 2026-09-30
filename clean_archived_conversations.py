"""Choose local archive deletion or the ChatGPT web archive recovery workflow.

Cloud deletion is confirmed in ChatGPT itself. This launcher can then clean exact,
user-confirmed deleted cloud IDs from the local catalog; it never extracts login data.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
import webbrowser

import cleaner_language as ui
from clean_codex_catalog import Audit, connect, validate_confirmed_deleted_plan


# Observed through Settings > Data controls in the current signed-in ChatGPT UI.
CLOUD_SETTINGS_URL = "https://chatgpt.com/settings/data-controls?view=archived-chats"
UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")


def choose_source() -> str | None:
    print(ui.text("1. 本機 Codex 封存對話\n2. ChatGPT 雲端封存對話\n3. 取消",
                  "1. Local Codex archives\n2. ChatGPT cloud archives\n3. Cancel"))
    choice = input(ui.text("請選擇 [1-3]: ", "Choose [1-3]: ")).strip()
    if choice in ("", "3"):
        return None
    if choice not in ("1", "2"):
        raise ValueError("Invalid archive source selection")
    return "local" if choice == "1" else "cloud"


def cloud_cache_selection(home: Path, ids: list[str]) -> dict:
    if not ids or any(not isinstance(key, str) or not UUID.fullmatch(key) for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("Specify distinct exact cloud conversation UUIDs")
    with closing(connect(home / "sqlite/codex-dev.db", "ro")) as db:
        hosts = db.execute("SELECT host_id FROM local_thread_catalog_hosts WHERE host_kind='chatgpt'").fetchall()
        if len(hosts) != 1:
            raise ValueError("Exactly one ChatGPT account is required; check the account before cleanup")
        entries = []
        for key in ids:
            rows = db.execute("SELECT host_id,display_title,project_id,source_kind FROM local_thread_catalog WHERE thread_id=?",
                              (key,)).fetchall()
            if len(rows) != 1 or rows[0][0] != hosts[0][0] or rows[0][3] != "chatgpt":
                raise ValueError(f"ID is absent, local, or belongs to another account: {key}")
            if not isinstance(rows[0][1], str) or not rows[0][1]:
                raise ValueError(f"Cached title is not available for review: {key}")
            entries.append({"id": key, "title": rows[0][1], "project_id": rows[0][2]})
        plan = {"schema_version": 1, "scope": "confirmed_deleted_chatgpt_cache",
                "confirmed_deleted_in_cloud": False, "host_id": hosts[0][0], "entries": entries}
        validate_confirmed_deleted_plan(db, plan)
    return plan


def run_child(script: str, args, flags: list[str], audit: Audit) -> int:
    command = [sys.executable, "-X", "utf8", "-u", str(Path(__file__).resolve().parent / script),
               "--lang", args.lang, "--codex-home", str(args.codex_home),
               "--output-dir", str(args.output_dir), *flags]
    if script == "delete_archived_threads.py" and args.codex:
        command.extend(["--codex", args.codex])
    result = subprocess.run(command, check=False)
    audit.record("archive_stage_finished", script=script, exit_code=result.returncode)
    return result.returncode


def cloud_workflow(args, audit: Audit) -> int:
    print(ui.text("1. 開啟 ChatGPT, 從封存管理逐筆刪除\n2. 清理已在網頁刪除, 但 Codex 仍顯示的索引\n3. 取消",
                  "1. Open ChatGPT and delete individual archives in its manager\n2. Clean Codex entries already deleted in ChatGPT\n3. Cancel"))
    choice = input(ui.text("請選擇 [1-3]: ", "Choose [1-3]: ")).strip()
    if choice in ("", "3"):
        return 0
    if choice == "1":
        print(ui.text("請使用相同帳號, 在 資料控制 > 已封存的對話 > 管理 核對標題與日期, 再逐筆按刪除並確認",
                      "Use the same account. In Data controls > Archived chats > Manage, check each title and date, then delete and confirm individually"))
        print(ui.text("此選項只開啟管理入口; 網頁刪除完成後, 可再次選第 2 項處理 Codex 殘留索引",
                      "This option opens the manager entry point. After deletion in ChatGPT, choose option 2 to handle remaining Codex entries"))
        opened = webbrowser.open(CLOUD_SETTINGS_URL)
        audit.record("cloud_archive_manager_handoff", url=CLOUD_SETTINGS_URL, browser_opened=opened,
                     cloud_deleted=False, live_cloud_verified=False, status="manual_cloud_deletion_pending")
        if not opened:
            print(ui.text("無法自動開啟瀏覽器, 請自行開啟: {url}", "Could not open a browser. Open: {url}", url=CLOUD_SETTINGS_URL))
        return 2
    if choice != "2":
        raise ValueError("Invalid cloud archive operation")
    print(ui.text("僅輸入已在相同帳號永久刪除的對話 ID; 無法載入或清單缺席不能當成刪除證據",
                  "Enter only IDs permanently deleted in the same ChatGPT account. Loading errors or absence from a list are insufficient"))
    raw = input(ui.text("精確 ID, 以逗號或空白分隔 (空白取消): ", "Exact IDs, separated by commas or spaces (empty to cancel): ")).strip()
    if not raw:
        return 0
    plan = cloud_cache_selection(args.codex_home, [key for key in re.split(r"[\s,]+", raw) if key])
    print(ui.text("本機帳戶: {host}", "Local account: {host}", host=plan["host_id"]))
    for entry in plan["entries"]:
        print(f"{entry['id']}  {entry['title']}  {entry['project_id'] or '-'}")
    # A read-only selection is not yet a statement that deletion happened in ChatGPT.
    audit.record("cloud_archive_cache_preview", host_id=plan["host_id"], entries=plan["entries"],
                 cloud_deletion_confirmed=False, live_cloud_verified=False)
    answer = input(ui.text("確認以上對話已在 ChatGPT 永久刪除, 並同意備份後移除這些本機索引; 輸入 CLOUD DELETED: ",
                           "Confirm these chats were permanently deleted in ChatGPT and allow backed-up local cache cleanup. Type CLOUD DELETED: ")).strip()
    if answer != "CLOUD DELETED":
        audit.record("archive_deletion_cancelled")
        return 0
    plan["confirmed_deleted_in_cloud"] = True
    path = audit.directory / "confirmed-cloud-deleted-plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    audit.record("cloud_deletion_confirmed_by_user", selected_ids=[entry["id"] for entry in plan["entries"]],
                 evidence_source="explicit_user_confirmation", live_cloud_verified=False, cloud_modified=False)
    flags = ["--confirmed-deleted-plan", str(path), "--wait-seconds", str(args.wait_seconds)]
    code = run_child("clean_codex_catalog.py", args, flags, audit)
    if code:
        return code
    return run_child("clean_codex_catalog.py", args, [*flags, "--apply"], audit)


def main() -> int:
    parser = ui.parser("清理無法刪除的已封存對話: 本機刪除 / 雲端管理與殘留索引修復", __doc__)
    parser.add_argument("--interactive", action="store_true", required=True)
    parser.add_argument("--source", choices=("local", "cloud"))
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--codex", help="Installed Codex executable for local deletion")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    ui.configure(args)
    if args.wait_seconds < 0:
        parser.error("Wait timeout cannot be negative")
    args.codex_home = args.codex_home.resolve()
    args.output_dir = args.output_dir.resolve()
    audit = Audit(args.output_dir / "logs" / ("archive-recovery-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    try:
        source = args.source or choose_source()
        if source is None:
            return 0
        audit.record("archive_recovery_source_selected", source=source)
        if source == "local":
            return run_child("delete_archived_threads.py", args, ["--interactive", "--allow-missing-rollouts"], audit)
        return cloud_workflow(args, audit)
    except Exception:
        audit.record("archive_recovery_failed", traceback=traceback.format_exc())
        print(ui.text("清理已停止; 日誌: {path}", "Recovery stopped. Log: {path}", path=audit.path))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
