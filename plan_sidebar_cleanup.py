"""從截圖標題及本機歷史索引建立精確對話清理清單, 預設唯讀

OCR 或相似標題不構成刪除證據; 模糊比對只提供建議
選定精確 ID 並確認後, 匯出供 Codex app 原生工具執行的封存與還原請求
本程式不呼叫雲端 API, 不永久刪除對話
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
import unicodedata

from clean_codex_catalog import Audit, connect
import cleaner_language as ui


UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")


def normalize(title: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", title).casefold() if not char.isspace())


def load_reviewed_plan(path: Path, host: str) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(plan, dict) or plan.get("schema_version") != 1
            or plan.get("confirmed_unwanted_sidebar_entries") is not True
            or plan.get("expected_chatgpt_host") != host):
        raise ValueError("A reviewed cloud sidebar plan for the current exact account is required.")
    ids, matches = plan.get("selected_ids"), plan.get("matches")
    if (not isinstance(ids, list) or not ids or any(not isinstance(key, str) or not UUID.fullmatch(key) for key in ids)
            or len(ids) != len(set(ids)) or not isinstance(matches, list)):
        raise ValueError("Reviewed plan requires distinct exact IDs and matching metadata.")
    entries = {}
    for item in matches:
        if (not isinstance(item, dict) or not isinstance(item.get("thread_id"), str)
                or not UUID.fullmatch(item["thread_id"]) or not isinstance(item.get("title"), str)
                or "project_id" not in item or (item["project_id"] is not None and not isinstance(item["project_id"], str))):
            raise ValueError("Invalid reviewed conversation metadata.")
        if item["thread_id"] in entries:
            raise ValueError("Duplicate matching ID in reviewed plan.")
        entries[item["thread_id"]] = item
    protected_ids = plan.get("protected_ids", [])
    if (not isinstance(protected_ids, list)
            or any(not isinstance(key, str) or not UUID.fullmatch(key) for key in protected_ids)):
        raise ValueError("Protected IDs must be an array of exact conversation IDs.")
    protected = set(protected_ids)
    if not set(ids) <= entries.keys() or set(ids) & protected:
        raise ValueError("Selected IDs are unmatched or protected.")
    return {"host_id": host, "entries": [entries[key] for key in ids]}


def archive_status(plan: dict, snapshot: dict | None) -> dict:
    entries = {item["thread_id"]: item for item in plan["entries"]}
    verified = set()
    if snapshot is not None:
        if (not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1
                or snapshot.get("source") != "codex_app.list_archived_threads"
                or snapshot.get("expected_chatgpt_host") != plan["host_id"]):
            raise ValueError("Archive verification requires a native app listing from the same account.")
        captured = datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
        if captured.tzinfo is None or not -300 <= (datetime.now(timezone.utc) - captured).total_seconds() <= 3600:
            raise ValueError("Archive snapshot must have a timezone and be no older than one hour.")
        conversations = snapshot.get("conversations")
        if not isinstance(conversations, list):
            raise ValueError("Archive listing conversations are required; acknowledgements alone are insufficient.")
        seen = set()
        for item in conversations:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not UUID.fullmatch(item["id"])
                    or item["id"] in seen or item.get("archived") is not True):
                raise ValueError("Archive listing requires distinct archived conversation IDs.")
            seen.add(item["id"])
            expected = entries.get(item["id"])
            if expected is not None:
                if item.get("title") != expected["title"] or item.get("project_id") != expected["project_id"]:
                    raise ValueError(f"Archived conversation metadata changed: {item['id']}. Review again.")
                verified.add(item["id"])
    pending = [key for key in entries if key not in verified]
    return {"schema_version": 1, "expected_chatgpt_host": plan["host_id"], "verified_ids": sorted(verified),
            "pending_ids": pending, "archive_listing_verified": not pending,
            "desktop_sidebar_verified": False, "permanent_deletions": 0,
            "archive_requests": [{"tool": "set_thread_archived", "arguments": {
                "source": "chatgpt", "threadId": key, "archived": True}} for key in pending]}


def inventory(home: Path, log_dir: Path) -> tuple[dict[str, dict], set[str], str]:
    rows = {}
    for path in sorted(log_dir.glob("catalog-run-*/project-scan.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for item in data["inventory"]:
            if item.get("source_kind") != "chatgpt":
                continue
            if not isinstance(item.get("thread_id"), str) or not UUID.fullmatch(item["thread_id"]) or not isinstance(item.get("title"), str):
                raise ValueError(f"Unsupported inventory: {path}")
            rows[item["thread_id"]] = item
    with closing(connect(home / "sqlite/codex-dev.db", "ro")) as db:
        hosts = db.execute("SELECT host_id FROM local_thread_catalog_hosts WHERE host_kind='chatgpt'").fetchall()
        if len(hosts) != 1:
            raise ValueError("Exactly one current ChatGPT account is required.")
        host = hosts[0][0]
        current = db.execute("SELECT thread_id,display_title,project_id FROM local_thread_catalog "
                             "WHERE source_kind='chatgpt' AND host_id=? AND missing_candidate=0", (host,)).fetchall()
    for key, title, project in current:
        rows[key] = {"thread_id": key, "title": title, "project_id": project, "source_kind": "chatgpt"}
    return rows, {key for key, _, _ in current}, host


def build_plan(images: list[dict], rows: dict[str, dict], protected: set[str], *,
               keep_through: str | None = None, corrections: dict[str, str] | None = None,
               reviewed_ids: list[str] | None = None, confirmed: bool = False) -> dict:
    corrections = corrections or {}
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in corrections.items()):
        raise ValueError("Title corrections must map image:line to a verified exact title.")
    by_title = {}
    for key, item in rows.items():
        if item.get("source_kind") == "chatgpt":
            by_title.setdefault(normalize(item["title"]), []).append(key)
    seen_images, seen_titles = set(), set()
    matches, unmatched, retained, ambiguous = [], [], [], []
    boundary_found = keep_through is None
    for image in images:
        if not isinstance(image.get("name"), str) or not isinstance(image.get("lines"), list) or any(not isinstance(line, str) for line in image["lines"]):
            raise ValueError("Unsupported screenshot text schema.")
        signature = image.get("sha256") or json.dumps(image["lines"], ensure_ascii=False)
        if signature in seen_images:
            continue
        seen_images.add(signature)
        lines = [corrections.get(f"{image['name']}:{index}", title) for index, title in enumerate(image["lines"], 1)]
        boundary = next((index for index, title in enumerate(lines) if keep_through is not None and normalize(title) == normalize(keep_through)), None)
        if boundary is not None:
            boundary_found = True
        for index, title in enumerate(lines):
            value = normalize(title)
            if boundary is not None and index <= boundary:
                retained.append({"title": title, "reason": "at_or_above_keep_boundary"})
                continue
            if value in seen_titles:
                continue
            seen_titles.add(value)
            ids = by_title.get(value, [])
            if not ids:
                suggestions = difflib.get_close_matches(value, by_title, n=3, cutoff=0.6)
                unmatched.append({"image": image["name"], "line": index + 1, "title": title,
                                  "suggestions": [rows[by_title[item][0]]["title"] for item in suggestions]})
                continue
            protected_ids = [key for key in ids if key in protected]
            if protected_ids:
                retained.append({"title": title, "ids": protected_ids, "reason": "current_catalog_entry"})
            eligible = [key for key in ids if key not in protected]
            if len(eligible) > 1:
                ambiguous.append({"title": title, "ids": eligible})
            matches.extend({"thread_id": key, "title": rows[key]["title"], "project_id": rows[key].get("project_id"),
                            "image": image["name"], "line": index + 1} for key in eligible)
    if not boundary_found:
        raise ValueError("Keep boundary not found; correct the screenshot text before reviewing.")
    boundary_ids = [key for key in protected if keep_through is not None
                    and key in rows and normalize(rows[key]["title"]) == normalize(keep_through)]
    if len(boundary_ids) > 1:
        raise ValueError("Keep boundary matches multiple current conversations; review the exact ID before archiving.")
    available = {item["thread_id"] for item in matches}
    selected = reviewed_ids or []
    if (not isinstance(selected, list) or len(selected) != len(set(selected))
            or any(not isinstance(key, str) or not UUID.fullmatch(key) for key in selected)
            or not set(selected) <= available or (selected and not confirmed)):
        raise ValueError("Review distinct exact matching IDs and explicitly confirm unwanted sidebar entries.")
    return {"schema_version": 1, "read_only": True, "unique_images": len(seen_images),
            "matches": matches, "same_title_candidates": ambiguous, "unmatched": unmatched,
            "retained": retained, "selected_ids": selected, "confirmed_unwanted_sidebar_entries": confirmed,
            "protected_ids": sorted(protected),
            "keep_through_id": boundary_ids[0] if boundary_ids else None,
            "automatic_removals": [], "live_cloud_verified": False, "desktop_sidebar_verified": False,
            "archive_requests": [{"tool": "set_thread_archived", "arguments": {
                "source": "chatgpt", "threadId": key, "archived": True}} for key in selected],
            "restore_requests": [{"tool": "set_thread_archived", "arguments": {
                "source": "chatgpt", "threadId": key, "archived": False}} for key in selected],
            "limitations": ["Historical IDs and exact titles require review; fuzzy suggestions are never selected.",
                            "OCR can omit entire rows; verify the source images and live sidebar before declaring coverage complete.",
                            "Current catalog entries and the keep boundary are protected.",
                            "Archive requests must be executed through the signed-in Codex app and verified in its archive list.",
                            "No cloud deletion is inferred from screenshots or missing cache rows."]}


def main() -> int:
    parser = ui.parser(__doc__, "Build exact reviewed archive requests from sidebar screenshots or verified titles. Preview only; no cloud changes.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--images", nargs="+", type=Path)
    source.add_argument("--ocr-json", type=Path)
    source.add_argument("--titles-file", type=Path, help="JSON array of verified screenshot titles")
    source.add_argument("--interactive", action="store_true")
    parser.add_argument("--keep-through-title")
    parser.add_argument("--title-corrections", type=Path)
    parser.add_argument("--reviewed-ids", type=Path)
    parser.add_argument("--confirmed-unwanted", action="store_true")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    ui.configure(args)
    audit = Audit(args.output_dir / "logs" / ("screenshot-plan-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    try:
        if args.interactive:
            if sys.platform == "win32":
                args.images = []
                print(ui.text("逐行輸入截圖檔案路徑; 空白結束", "Enter screenshot file paths, one per line. Empty line finishes."))
                while value := input(ui.text("截圖: ", "Image: ")).strip().strip('"'):
                    args.images.append(Path(value))
                if not args.images:
                    return 0
            else:
                value = input(ui.text("已核對標題的 JSON 檔案 (空白取消): ", "Verified titles JSON file (empty to cancel): ")).strip().strip('"')
                if not value:
                    return 0
                args.titles_file = Path(value)
            args.keep_through_title = input(ui.text("保留至此對話的完整標題 (空白表示不設分界): ", "Keep through this exact title (empty for no boundary): ")).strip() or None
        if args.images:
            if sys.platform != "win32":
                raise ValueError("Windows OCR is available only on Windows; use --titles-file or --ocr-json on other platforms.")
            args.ocr_json = audit.directory / "ocr.json"
            command = [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                       "-NoProfile", "-File", str(Path(__file__).parent / "scripts/read_sidebar_images.ps1"),
                       "-OutputPath", str(args.ocr_json.resolve()), *[str(path.resolve(strict=True)) for path in args.images]]
            subprocess.run(command, check=True)
        images = ([{"name": args.titles_file.name, "lines": json.loads(args.titles_file.read_text(encoding="utf-8-sig"))}]
                  if args.titles_file else json.loads(args.ocr_json.read_text(encoding="utf-8-sig")))
        rows, protected, host = inventory(args.codex_home, args.output_dir / "logs")
        corrections = json.loads(args.title_corrections.read_text(encoding="utf-8-sig")) if args.title_corrections else None
        reviewed = json.loads(args.reviewed_ids.read_text(encoding="utf-8-sig")) if args.reviewed_ids else None
        plan = build_plan(images, rows, protected, keep_through=args.keep_through_title,
                          corrections=corrections, reviewed_ids=reviewed, confirmed=args.confirmed_unwanted)
        if args.interactive and plan["matches"]:
            print(ui.text("以下候選項目已排除保留分界與目前有效索引", "Exact candidates below exclude the keep boundary and current catalog entries."))
            for index, item in enumerate(plan["matches"], 1):
                print(f"{index}. {item['title']} | {item['thread_id']} | project={item['project_id']}")
            if plan["unmatched"]:
                print(ui.text("已排除 {count} 個無法比對的標題; 請核對 OCR 結果", "{count} unmatched titles were excluded. Review OCR corrections before including them.", count=len(plan['unmatched'])))
            answer = input(ui.text("建立可還原的雲端封存請求; 輸入項目編號 (空白分隔), ALL 或空白只預覽: ", "Prepare reversible cloud archive requests. Select numbers separated by spaces, ALL, or empty to preview only: ")).strip()
            if answer:
                numbers = list(range(1, len(plan["matches"]) + 1)) if answer == "ALL" else [int(value) for value in answer.split()]
                if any(number < 1 or number > len(plan["matches"]) for number in numbers):
                    raise ValueError("Invalid candidate selection.")
                reviewed = [plan["matches"][number - 1]["thread_id"] for number in numbers]
                plan = build_plan(images, rows, protected, keep_through=args.keep_through_title,
                                  corrections=corrections, reviewed_ids=reviewed, confirmed=True)
        plan["expected_chatgpt_host"] = host
        path = audit.directory / "review-plan.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        audit.record("screenshot_cleanup_plan", images=plan["unique_images"], matching_ids=len(plan["matches"]),
                     same_title_groups=len(plan["same_title_candidates"]), unmatched=len(plan["unmatched"]),
                     selected=len(plan["selected_ids"]), cloud_modified=False, plan=str(path))
        print(ui.text("核對清單: {path}\n尚未修改對話; 請透過已登入的 Codex app 執行封存請求, 再核對封存清單", "Review plan: {path}\nNo conversations changed. Execute reviewed archive requests through Codex app, then verify its archive list.", path=path))
        return 0
    except Exception:
        audit.record("screenshot_plan_failed", traceback=traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
