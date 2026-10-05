"""清除明確選定的側邊欄舊專案參照, 不修改雲端專案或對話

預設只預覽快取中缺少的專案參照, --interactive 選定後等待桌面退出再備份清理
缺少快取不是雲端刪除證據, 必須由使用者確認要移除的側邊欄參照
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import time
import traceback

from clean_codex_catalog import Audit, app_running
import cleaner_language as ui


KEY = "sidebar-custom-sections-v3"
PROJECT = re.compile(r"g-p-[0-9a-f]{32}")


def account_sections(state: dict, account: str) -> list[dict]:
    value = state["electron-persisted-atom-state"][KEY][account]
    sections = value.get("sections")
    if not isinstance(sections, list) or any(
        not isinstance(section, dict) or not isinstance(section.get("id"), str)
        or not isinstance(section.get("itemKeys"), list)
        or any(not isinstance(key, str) for key in section["itemKeys"])
        for section in sections
    ) or len({section["id"] for section in sections}) != len(sections):
        raise ValueError("Unsupported sidebar section schema; nothing changed.")
    return sections


def candidates(state: dict, account: str) -> list[dict]:
    sections = account_sections(state, account)
    cached = state["electron-persisted-atom-state"].get("chatgpt-sidebar-state-v1", {}).get(account, {})
    projects = cached.get("projects")
    pins = cached.get("pinnedProjects")
    if (not isinstance(projects, list) or not isinstance(pins, list)
            or any(not isinstance(pin, dict) for pin in pins)):
        raise ValueError("No project cache reference available; select exact project IDs with a reviewed plan.")
    known = set()
    for project in [*projects, *[pin.get("project") for pin in pins]]:
        if not isinstance(project, dict) or not isinstance(project.get("id"), str):
            raise ValueError("Unsupported project cache schema.")
        known.add(project["id"])
    return [{"section_id": section["id"], "section_name": section.get("name"),
             "project_id": key.removeprefix("chatgpt:project:")}
            for section in sections for key in section["itemKeys"]
            if key.startswith("chatgpt:project:") and key[16:] not in known]


def make_plan(state: dict, account: str, ids: list[str]) -> dict:
    if not ids or len(ids) != len(set(ids)) or any(not isinstance(key, str) or not PROJECT.fullmatch(key) for key in ids):
        raise ValueError("Select distinct exact ChatGPT project IDs.")
    selected = {"chatgpt:project:" + project for project in ids}
    references = [{"section_id": section["id"], "item_key": key}
                  for section in account_sections(state, account) for key in section["itemKeys"]
                  if key in selected]
    if {ref["item_key"][16:] for ref in references} != set(ids):
        raise ValueError("Some selected projects have no sidebar reference.")
    return {"schema_version": 1, "account_id": account, "project_ids": ids,
            "references": references, "confirmed_unwanted_sidebar_references": True}


def transform(state: dict, plan: dict) -> dict:
    if plan.get("schema_version") != 1 or plan.get("confirmed_unwanted_sidebar_references") is not True:
        raise ValueError("An exact reviewed sidebar reference plan is required.")
    account, ids = plan.get("account_id"), plan.get("project_ids")
    if not isinstance(account, str) or not isinstance(ids, list):
        raise ValueError("Invalid sidebar reference plan.")
    if make_plan(state, account, ids) != plan:
        raise RuntimeError("Selected sidebar references changed; preview again.")
    result = copy.deepcopy(state)
    remove = {"chatgpt:project:" + key for key in ids}
    for section in account_sections(result, account):
        section["itemKeys"] = [key for key in section["itemKeys"] if key not in remove]
    return result


def apply_plan(path: Path, plan: dict, backup: Path, audit: Audit) -> None:
    if app_running():
        raise RuntimeError("Close Codex/ChatGPT before updating sidebar references.")
    before = path.read_bytes()
    after = transform(json.loads(before), plan)
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("xb") as stream:
        stream.write(before)
        stream.flush()
        os.fsync(stream.fileno())
    audit.record("sidebar_reference_backup", path=str(backup))
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            json.dump(after, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        if app_running() or path.read_bytes() != before:
            raise RuntimeError("Desktop or sidebar state changed during backup; nothing replaced.")
        os.replace(temp, path)
        if json.loads(path.read_bytes()) != after:
            raise RuntimeError(f"Sidebar readback failed; original backup: {backup}")
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
    audit.record("sidebar_references_removed", account_id=plan["account_id"],
                 project_count=len(plan["project_ids"]), reference_count=len(plan["references"]),
                 cloud_modified=False, catalog_modified=False, desktop_sidebar_verified=False)


def main() -> int:
    parser = ui.parser(__doc__, "Review and remove exact stale sidebar project references. Cloud projects and conversations are preserved.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--plan", type=Path)
    selection.add_argument("--interactive", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--account-id")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    ui.configure(args)
    if args.wait_seconds < 0 or (args.apply and args.plan is None and not args.interactive):
        parser.error("--apply requires a reviewed --plan or --interactive; wait time cannot be negative")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    audit = Audit(args.output_dir / "logs" / ("sidebar-references-" + stamp))
    try:
        path = args.codex_home.resolve(strict=True) / ".codex-global-state.json"
        state = json.loads(path.read_bytes())
        plan = json.loads(args.plan.read_text(encoding="utf-8-sig")) if args.plan else None
        accounts = state["electron-persisted-atom-state"][KEY]
        account = plan["account_id"] if plan else args.account_id
        if account is None:
            if len(accounts) != 1:
                raise ValueError("Select an exact --account-id; multiple accounts found.")
            account = next(iter(accounts))
        if plan is None:
            rows = candidates(state, account)
            audit.record("sidebar_reference_candidates", candidates=rows, live_cloud_verified=False,
                         limitation="Missing project cache entries do not prove deletion.")
            ids = list(dict.fromkeys(row["project_id"] for row in rows))
            for index, key in enumerate(ids, 1):
                sections = ", ".join(str(row["section_name"]) for row in rows if row["project_id"] == key)
                print(f"{index}. {sections}: {key}", flush=True)
            if not args.interactive or not ids:
                print(ui.text("僅預覽, 缺少快取不是刪除證據, 使用 --interactive 選擇側邊欄參照", "Preview only. Missing cache is not deletion evidence. Use --interactive to select sidebar references."), flush=True)
                return 0
            answer = input(ui.text("只移除側邊欄參照, 輸入項目編號 (空白分隔), ALL 或空白取消: ", "Remove sidebar references only. Enter numbers separated by spaces, ALL, or empty to cancel: ")).strip()
            if not answer:
                return 0
            if answer != "ALL":
                numbers = [int(value) for value in answer.split()]
                if not numbers or any(number < 1 or number > len(ids) for number in numbers):
                    raise ValueError("Invalid selection.")
                ids = [ids[number - 1] for number in numbers]
            plan = make_plan(state, account, ids)
            args.apply = True
        transform(state, plan)
        (audit.directory / "removal-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        audit.record("sidebar_reference_plan", **plan, apply=args.apply)
        if not args.apply:
            return 0
        print(ui.text("請完全退出 Codex/ChatGPT 並保留此外部視窗, 等待備份並清除已選參照", "Close Codex/ChatGPT. Keep this external console open; waiting to back up and remove the selected references."), flush=True)
        deadline = time.monotonic() + args.wait_seconds
        while app_running():
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop still running; sidebar references were not changed.")
            time.sleep(2)
        apply_plan(path, plan, args.output_dir / "backups" / ("sidebar-state-" + stamp + ".json"), audit)
        print(ui.text("已移除選定的側邊欄參照, 重開 Codex 更新畫面, 雲端對話未變更", "Selected sidebar references removed. Reopen Codex to refresh; cloud conversations were not changed."), flush=True)
        return 0
    except Exception:
        audit.record("sidebar_reference_cleanup_failed", traceback=traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
