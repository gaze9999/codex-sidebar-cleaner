"""依全部桌面日誌清理已確認 conversation_deleted 的 ChatGPT 側欄索引。

python clean_codex_catalog.py          唯讀檢查並留下 log
python clean_codex_catalog.py --apply  等待桌面退出，備份後以交易清理
python clean_codex_catalog.py --apply --reconcile  重設雲端清單同步，交由 App 核對最近約 30 天

Python 3.10+ / Windows 或 macOS。紀錄寫入 logs/catalog-run-*，備份寫入 backups/。
資料庫備份可能包含私人側欄資訊，請留在本機。
"""

from __future__ import annotations

import argparse
import csv
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback
from typing import TypedDict, cast
import cleaner_language as ui


def discover_deleted(roots: list[Path]) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    seen: set[str] = set()
    decoder = json.JSONDecoder()
    for root in roots:
        for source in sorted(root.rglob("codex-desktop-*.log")):
            if source.name in seen:
                continue
            seen.add(source.name)
            with source.open(encoding="utf-8", errors="replace") as stream:
                for number, line in enumerate(stream, 1):
                    if "sa_server_request_failed" not in line or "errorCode=conversation_deleted " not in line:
                        continue
                    marker = "errorMessage="
                    if marker not in line:
                        continue
                    # 僅接受伺服器結構化錯誤；一般 404/inaccessible 不視為已刪除。
                    try:
                        body, _ = decoder.raw_decode(line.split(marker, 1)[1])
                    except json.JSONDecodeError:
                        continue
                    detail = body.get("detail") if isinstance(body, dict) else None
                    if not isinstance(detail, dict) or detail.get("code") != "conversation_deleted":
                        continue
                    thread_id = detail.get("conversation_id")
                    if not isinstance(thread_id, str) or not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", thread_id):
                        continue
                    evidence[thread_id] = {"source": str(source), "line": number,
                                           "timestamp": line.split(" ", 1)[0]}
    return evidence


def log_roots() -> list[Path]:
    if sys.platform == "win32":
        local = Path(os.environ["LOCALAPPDATA"])
        roots = [p / "LocalCache" / "Local" / "Codex" / "Logs"
                 for p in (local / "Packages").glob("OpenAI.Codex_*")]
        roots.append(local / "Codex" / "Logs")
        return roots
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Logs" / "com.openai.codex"]
    raise RuntimeError("Only Windows and macOS are supported.")


def previous_evidence(directory: Path) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for source in sorted(directory.glob("catalog-run-*/cleanup.jsonl")):
        with source.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # 中斷寫入留下的不完整紀錄不能當成刪除依據。
                    continue
                if not isinstance(event, dict) or event.get("event") not in {"log_evidence", "final_log_evidence"}:
                    continue
                entries = event.get("evidence", {})
                if not isinstance(entries, dict):
                    continue
                for thread_id, detail in entries.items():
                    if re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", thread_id) and isinstance(detail, dict):
                        evidence[thread_id] = detail
    return evidence


def discover_load_failures(roots: list[Path]) -> tuple[list[Path], dict[str, dict[str, object]]]:
    files = {source.resolve() for root in roots for source in root.rglob("codex-desktop-*.log")}
    if not files:
        return [], {}
    latest = max(files, key=lambda source: (source.stat().st_mtime_ns, str(source)))
    session = re.match(r"(codex-desktop-[0-9a-f-]{36}-\d+)-t\d+-", latest.name)
    sources = sorted(source for source in files if source.parent == latest.parent and
                     (source.name.startswith(session[1] + "-t") if session else source == latest))
    events: dict[str, tuple[tuple[str, str, int], dict[str, object] | None]] = {}
    decoder = json.JSONDecoder()
    uuid = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
    for source in sources:
        with source.open(encoding="utf-8", errors="replace") as stream:
            for number, line in enumerate(stream, 1):
                detail = None
                if "sa_server_request_failed" in line and "errorMessage=" in line:
                    try:
                        body, _ = decoder.raw_decode(line.split("errorMessage=", 1)[1])
                    except json.JSONDecodeError:
                        continue
                    error = body.get("detail") if isinstance(body, dict) else None
                    if not isinstance(error, dict):
                        continue
                    thread_id, code = error.get("conversation_id"), error.get("code")
                    if not isinstance(thread_id, str) or not re.fullmatch(uuid, thread_id) or not isinstance(code, str):
                        continue
                    detail = {"source_kind": "chatgpt", "error_code": code,
                              "message": error.get("message"),
                              "category": "confirmed_deleted" if code == "conversation_deleted" else "cloud_load_failed"}
                elif re.search(r"\bmethod=thread/(?:read|resume)(?:\s|$)", line):
                    match = re.search(rf"\bconversationId=({uuid})(?:\s|$)", line)
                    if match is None:
                        continue
                    thread_id = match[1]
                    if "response_routed" in line and re.search(r"\berrorCode=null(?:\s|$)", line):
                        # 後續成功的載入不再列為本次 session 的未解錯誤。
                        detail = None
                    elif "Request failed" in line and "error=" in line:
                        try:
                            error, _ = decoder.raw_decode(line.split("error=", 1)[1])
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                            continue
                        detail = {"source_kind": "codex", "error_code": error.get("code"),
                                  "message": error["message"], "category": "thread_load_failed"}
                    else:
                        continue
                else:
                    continue
                timestamp = line.split(" ", 1)[0]
                order = (timestamp, str(source), number)
                if thread_id not in events or order > events[thread_id][0]:
                    if detail is not None:
                        detail.update(source=str(source), line=number, timestamp=timestamp)
                    events[thread_id] = (order, detail)
    return sources, {key: detail for key, (_, detail) in events.items() if detail is not None}


def diagnose_load_failures(connection: sqlite3.Connection, roots: list[Path], audit: Audit) -> dict:
    sources, failures = discover_load_failures(roots)
    entries = []
    for thread_id, failure in sorted(failures.items()):
        rows = connection.execute(
            "SELECT host_id,display_title,source_kind,project_id FROM local_thread_catalog WHERE thread_id=?",
            (thread_id,),
        ).fetchall()
        entries.append({"thread_id": thread_id, **failure,
                        "catalog_entries": [{"host_id": host, "title": title, "source_kind": kind,
                                             "project_id": project} for host, title, kind, project in rows],
                        "confirmed_deleted_catalog_target": failure["category"] == "confirmed_deleted"
                        and len(rows) == 1 and rows[0][2] == "chatgpt"})
    report = {"read_only": True, "scope": "latest_desktop_log_session",
              "sources": [str(source) for source in sources], "failures": entries,
              "automatic_removals": [], "live_cloud_verified": False, "ui_verified": False,
              "limitations": [
                  "Logged load failures are not proof of cloud deletion or current sidebar visibility.",
                  "Permission errors, not-found responses, unloaded threads and timeouts are not deletion evidence.",
                  "Project lists fetched directly by the app can contain entries absent from the local catalog; catalog cleanup cannot remove those list items.",
              ]}
    path = audit.directory / "load-diagnostics.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    outside = sum(not entry["catalog_entries"] for entry in entries)
    audit.record("load_diagnostics", logged_failures=len(entries), failures_outside_catalog=outside,
                 sources_found=len(sources), report=str(path), automatic_removals=0,
                 live_cloud_verified=False, ui_verified=False)
    if entries:
        print(ui.text("最新 App 日誌有 {count} 個對話載入錯誤; {outside} 個不在本機索引; 這些錯誤不是刪除證據; 報告: {path}", "Latest app session: {count} logged conversation load failures; {outside} have no local catalog row. These errors are not deletion evidence. Report: {path}", count=len(entries), outside=outside, path=path), flush=True)
    elif not sources:
        print(ui.text("找不到桌面日誌; 無法診斷對話載入錯誤", "No desktop logs found. Conversation load errors could not be diagnosed."), flush=True)
    return report


def validate_schema(connection: sqlite3.Connection) -> None:
    required = {
        "local_thread_catalog": {"host_id", "thread_id", "display_title", "source_kind", "project_id", "missing_candidate"},
        "local_thread_catalog_metadata": {"id", "catalog_revision"},
        "local_thread_catalog_hosts": {"host_id", "host_kind"},
        "local_thread_catalog_sync_state": {"host_id", "initial_build_complete", "last_full_reconciled_at", "watermark_updated_at"},
        "local_thread_catalog_scan_checkpoints": {"host_id", "checkpoint"},
        "local_thread_catalog_scan_entries": {"host_id", "thread_id", "removed"},
    }
    for table, columns in required.items():
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns <= actual:
            raise RuntimeError(f"Unsupported database schema: {table} lacks {sorted(columns-actual)}. Nothing changed.")


def verify(connection: sqlite3.Connection, ids: list[str], audit: Audit) -> None:
    integrity(connection)
    remaining = targets(connection, ids)
    counts = connection.execute(
        "SELECT CASE WHEN project_id IS NULL THEN 'outside_project' ELSE 'in_project' END, "
        "missing_candidate,count(*) FROM local_thread_catalog WHERE source_kind='chatgpt' GROUP BY 1,2"
    ).fetchall()
    sync = connection.execute(
        "SELECT s.initial_build_complete,s.last_full_reconciled_at, "
        "EXISTS(SELECT 1 FROM local_thread_catalog_scan_checkpoints p WHERE p.host_id=s.host_id) "
        "FROM local_thread_catalog_sync_state s JOIN local_thread_catalog_hosts h USING(host_id) "
        "WHERE h.host_kind='chatgpt'"
    ).fetchall()
    audit.record("verification", integrity_check="ok", known_deleted_ids=len(ids),
                 remaining_known_deleted=[{"thread_id": row[1], "title": row[2]} for row in remaining],
                 catalog_counts=[{"scope": scope, "missing_candidate": missing, "count": count}
                                 for scope, missing, count in counts],
                 sync_states=[{"complete": bool(complete), "last_full_reconciled_at_ms": last,
                               "checkpoint_pending": bool(pending)} for complete, last, pending in sync],
                 all_cloud_conversations_verified=False,
                 limitation="No per-conversation live lookup. Unseen failures and old/project-specific ghosts may remain.")
    print(ui.text("剩餘已確認刪除索引: {count}; 專案與同步數量請查看日誌", "Known deleted entries remaining: {count}. See log for project and sync counts.", count=len(remaining)), flush=True)


def scan_projects(connection: sqlite3.Connection, state_path: Path, audit: Audit) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if not isinstance(state, dict):
        raise RuntimeError("Invalid project state document.")
    assignments = state.get("thread-project-assignments", {})
    local_projects = state.get("local-projects", {})
    if not isinstance(assignments, dict) or not isinstance(local_projects, dict):
        raise RuntimeError("Unsupported project assignment structure.")
    rows = connection.execute(
        "SELECT host_id,thread_id,display_title,source_kind,project_id,missing_candidate,source_updated_at "
        "FROM local_thread_catalog ORDER BY source_updated_at DESC,host_id,thread_id"
    ).fetchall()
    inventory: list[dict[str, object]] = []
    by_id: dict[str, list[dict[str, object]]] = {}
    by_title: dict[str, list[dict[str, object]]] = {}
    external_assignments = 0
    for host, thread_id, title, kind, project_id, missing, updated in rows:
        assignment = assignments.get(thread_id)
        effective_project = project_id
        membership_source = "catalog" if project_id else "none"
        if host == "local" and kind != "chatgpt" and isinstance(assignment, dict):
            assigned_project = assignment.get("projectId")
            if assignment.get("projectKind") == "local" and assigned_project in local_projects:
                effective_project = assigned_project
                membership_source = "explicit_local_project_assignment"
                external_assignments += 1
        item = {"thread_id": thread_id, "title": title, "source_kind": kind,
                "project_id": effective_project, "membership_source": membership_source,
                "missing_candidate": missing,
                "updated_at": datetime.fromtimestamp(updated, timezone.utc).astimezone().isoformat(),
                "scope": "in_project" if effective_project else "outside_project"}
        inventory.append(item)
        by_id.setdefault(thread_id, []).append(item)
        normalized = "".join(title.split()).casefold()
        if normalized:
            by_title.setdefault(normalized, []).append(item)
    duplicate_ids = [items for items in by_id.values() if len(items) > 1]
    title_candidates = [items for items in by_title.values()
                        if len({item["thread_id"] for item in items}) > 1
                        and {item["scope"] for item in items} == {"in_project", "outside_project"}]
    counts: dict[str, int] = {}
    for item in inventory:
        group = f"{item['source_kind']}:{item['scope']}:missing={item['missing_candidate']}"
        counts[group] = counts.get(group, 0) + 1
    report = {"read_only": True, "catalog_rows_scanned": len(inventory),
              "explicit_local_assignments": external_assignments,
              "project_state_found": state_path.exists(), "counts": counts,
              "duplicate_id_groups": duplicate_ids, "same_title_candidates": title_candidates,
              "automatic_removals": [], "inventory": inventory,
              "limitations": ["Project and Recents views can share one catalog row; deleting it can affect both views.",
                              "Matching titles do not establish duplicate conversations.",
                              "This is local inventory, not live cloud existence verification or an exact visible UI snapshot."]}
    path = audit.directory / "project-scan.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = audit.directory / "project-scan.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["thread_id", "title", "source_kind", "project_id",
                                                  "membership_source", "missing_candidate", "updated_at", "scope"])
        writer.writeheader()
        # CSV 預覽可能由 Excel 開啟，避免標題被當成公式執行。
        for item in inventory:
            safe = {key: ("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value)
                    for key, value in item.items()}
            writer.writerow(safe)
    audit.record("project_scan_completed", rows=len(inventory), counts=counts,
                 duplicate_id_groups=len(duplicate_ids), same_title_candidate_groups=len(title_candidates),
                 automatic_removals=0, json_report=str(path), csv_report=str(csv_path), live_cloud_verified=False)


class Audit:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.path = directory / "cleanup.jsonl"

    def record(self, event: str, **fields: object) -> None:
        entry = {"time": datetime.now().astimezone().isoformat(), "event": event, **fields}
        # 每筆紀錄立即 flush/fsync，桌面退出時仍保留已完成的進度。
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(f"{entry['time']} {ui.event_message(event)}", flush=True)


def app_running() -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True,
            check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        rows = csv.reader(result.stdout.decode("utf-8", errors="replace").splitlines())
        return any(row and row[0].lower() in {"chatgpt.exe", "codex-desktop.exe"} for row in rows)
    if sys.platform == "darwin":
        # macOS pgrep -x can miss a main app whose process name is a truncated path.
        result = subprocess.run(["ps", "-axo", "command="], capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError("Could not inspect running apps; refusing to modify the database.")
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        pattern = re.compile(r"^\S*/(?:Codex|ChatGPT)\.app/Contents/MacOS/(?:Codex|ChatGPT)(?:\s|$)")
        return any(pattern.match(line.strip()) for line in lines)
    raise RuntimeError("Only Windows and macOS are supported.")


def connect(database: Path, mode: str) -> sqlite3.Connection:
    return sqlite3.connect(database.resolve(strict=True).as_uri() + f"?mode={mode}",
                           uri=True, timeout=10, isolation_level=None)


def integrity(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    if rows != [("ok",)]:
        raise RuntimeError(f"Database integrity failed: {rows}")


def targets(connection: sqlite3.Connection, ids: list[str]) -> list[tuple[str, str, str]]:
    rows = connection.execute(
        f"SELECT host_id, thread_id, display_title FROM local_thread_catalog "
        f"WHERE source_kind = 'chatgpt' AND thread_id IN ({','.join('?' for _ in ids)}) ORDER BY thread_id",
        ids,
    ).fetchall()
    if len({row[1] for row in rows}) != len(rows) or len({row[0] for row in rows}) > 1:
        raise RuntimeError("Ambiguous account/host matches; refusing cleanup.")
    return rows


def cleanup_requires_desktop_exit(*, apply: bool, reconcile: bool,
                                  rows: list[tuple[str, str, str]]) -> bool:
    return apply and (reconcile or bool(rows))


def retained_digest(connection: sqlite3.Connection, ids: list[str]) -> str:
    rows = connection.execute(
        f"SELECT * FROM local_thread_catalog WHERE NOT "
        f"(source_kind = 'chatgpt' AND thread_id IN ({','.join('?' for _ in ids)})) ORDER BY host_id, thread_id",
        ids,
    ).fetchall()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


class RecentsPlan(TypedDict):
    schema_version: int
    scope: str
    confirmed_complete_reference: bool
    host_id: str
    keep_ids: list[str]
    remove_ids: list[str]


class ConfirmedDeletedEntry(TypedDict):
    id: str
    title: str
    project_id: str | None


class ConfirmedDeletedPlan(TypedDict):
    schema_version: int
    scope: str
    confirmed_deleted_in_cloud: bool
    host_id: str
    entries: list[ConfirmedDeletedEntry]


def load_confirmed_deleted_plan(path: Path) -> ConfirmedDeletedPlan:
    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or plan.get("scope") != "confirmed_deleted_chatgpt_cache":
        raise RuntimeError("Unsupported confirmed-deleted plan.")
    if plan.get("confirmed_deleted_in_cloud") is not True or not isinstance(plan.get("host_id"), str) or not plan["host_id"]:
        raise RuntimeError("Plan requires explicit cloud deletion confirmation and exact host.")
    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("Plan requires at least one reviewed entry.")
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "title", "project_id"}:
            raise RuntimeError("Each entry requires only id, title, and project_id.")
        key = entry["id"]
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", key) or key in ids:
            raise RuntimeError("Invalid or duplicate confirmed-deleted ID.")
        if not isinstance(entry["title"], str) or not entry["title"]:
            raise RuntimeError("Each entry requires a nonempty title.")
        if entry["project_id"] is not None and not isinstance(entry["project_id"], str):
            raise RuntimeError("project_id must be a string or null.")
        ids.add(key)
    return cast(ConfirmedDeletedPlan, plan)


def validate_confirmed_deleted_plan(connection: sqlite3.Connection, plan: ConfirmedDeletedPlan) -> None:
    for entry in plan["entries"]:
        rows = connection.execute(
            "SELECT host_id,display_title,project_id,source_kind FROM local_thread_catalog WHERE thread_id=?",
            (entry["id"],),
        ).fetchall()
        if rows and rows != [(plan["host_id"], entry["title"], entry["project_id"], "chatgpt")]:
            raise RuntimeError(f"Catalog entry changed or account differs: {entry['id']}. Nothing changed.")


def load_recents_plan(path: Path) -> RecentsPlan:
    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or plan.get("scope") != "visible_nonproject_chatgpt":
        raise RuntimeError("Unsupported Recents plan.")
    if plan.get("confirmed_complete_reference") is not True or not isinstance(plan.get("host_id"), str):
        raise RuntimeError("Recents plan requires a confirmed complete reference and exact host.")
    for key in ("keep_ids", "remove_ids"):
        values = plan.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", v) for v in values):
            raise RuntimeError(f"Invalid {key} in Recents plan.")
        if len(set(values)) != len(values):
            raise RuntimeError(f"Duplicate IDs in {key}.")
    if set(plan["keep_ids"]) & set(plan["remove_ids"]):
        raise RuntimeError("Keep and remove IDs overlap.")
    return cast(RecentsPlan, plan)


def validate_recents_plan(connection: sqlite3.Connection, plan: RecentsPlan) -> None:
    actual = {r[0] for r in connection.execute(
        "SELECT thread_id FROM local_thread_catalog WHERE host_id=? AND source_kind='chatgpt' "
        "AND project_id IS NULL AND missing_candidate=0", (plan["host_id"],)
    )}
    expected = set(plan["keep_ids"]) | set(plan["remove_ids"])
    if actual == set(plan["keep_ids"]) and not targets(connection, plan["remove_ids"]):
        return
    if actual != expected:
        raise RuntimeError("Recents changed since the reference was confirmed. Refresh the plan; nothing changed.")


def cleanup(database: Path, audit: Audit, ids: list[str], backup: Path,
            recents_plan: RecentsPlan | None = None,
            confirmed_plan: ConfirmedDeletedPlan | None = None) -> None:
    if app_running():
        raise RuntimeError("Desktop is still running; no cleanup performed.")
    connection = connect(database, "rw")
    committed = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        integrity(connection)
        if recents_plan is not None:
            validate_recents_plan(connection, recents_plan)
            audit.record("confirmed_recents_alignment", keep_ids=recents_plan["keep_ids"],
                         remove_ids=ids, scope=recents_plan["scope"],
                         reason="User confirmed complete mobile Recents reference; cache-only alignment, not proof of cloud deletion.")
        if confirmed_plan is not None:
            validate_confirmed_deleted_plan(connection, confirmed_plan)
            audit.record("user_confirmed_cloud_deletion", entries=confirmed_plan["entries"],
                         host_id=confirmed_plan["host_id"],
                         reason="User-confirmed cloud deletion; local cache removal only.")
        if connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND "
                              "tbl_name IN ('local_thread_catalog','local_thread_catalog_metadata')").fetchall():
            raise RuntimeError("Unexpected triggers; refusing unknown side effects.")
        rows = targets(connection, ids)
        revision = connection.execute(
            "SELECT catalog_revision FROM local_thread_catalog_metadata WHERE id=1"
        ).fetchone()
        if revision is None or not isinstance(revision[0], int):
            raise RuntimeError("Missing or invalid catalog revision.")
        audit.record("plan", rows=[{"thread_id": r[1], "title": r[2]} for r in rows],
                     revision_before=revision[0])
        if not rows:
            connection.rollback()
            audit.record("no_op", reason="No matching stale rows remain.")
            return
        before_digest = retained_digest(connection, ids)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise FileExistsError(backup)
        # 持有寫入鎖時，另一個唯讀連線以 SQLite backup API 取得含 WAL 的一致備份。
        with closing(connect(database, "ro")) as reader, closing(sqlite3.connect(backup)) as destination:
            reader.backup(destination)
            integrity(destination)
        audit.record("backup_verified", path=str(backup),
                     sha256=hashlib.sha256(backup.read_bytes()).hexdigest())
        for host_id, thread_id, title in rows:
            cursor = connection.execute(
                "DELETE FROM local_thread_catalog WHERE host_id=? AND thread_id=? AND source_kind='chatgpt'",
                (host_id, thread_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Unexpected affected row count for {thread_id}")
            audit.record("delete_pending_commit", thread_id=thread_id, title=title)
        if connection.execute("UPDATE local_thread_catalog_metadata SET catalog_revision=catalog_revision+1 "
                              "WHERE id=1").rowcount != 1:
            raise RuntimeError("Revision update failed.")
        if targets(connection, ids) or retained_digest(connection, ids) != before_digest:
            raise RuntimeError("Target removal or unrelated catalog preservation check failed.")
        if connection.total_changes != len(rows) + 1:
            raise RuntimeError("Unexpected database change count.")
        integrity(connection)
        if app_running():
            raise RuntimeError("Desktop restarted before commit; rolling back.")
        connection.commit()
        committed = True
        audit.record("committed", deleted_count=len(rows), revision_after=revision[0]+1,
                     retained_catalog_sha256=before_digest, ui_verified=False)
        integrity(connection)
        if targets(connection, ids):
            raise RuntimeError("Target entries appeared again after commit.")
        audit.record("completed", integrity_check="ok", targets_remaining=0, ui_verified=False)
    except Exception:
        if not committed:
            connection.rollback()
        audit.record("cleanup_failed", committed=committed, traceback=traceback.format_exc())
        raise
    finally:
        connection.close()


def reset_sync(database: Path, audit: Audit, backup: Path) -> None:
    if app_running():
        raise RuntimeError("Desktop is still running; sync state was not changed.")
    connection = connect(database, "rw")
    committed = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        integrity(connection)
        hosts = connection.execute(
            "SELECT host_id FROM local_thread_catalog_hosts WHERE host_kind='chatgpt'"
        ).fetchall()
        if len(hosts) != 1:
            raise RuntimeError("Expected exactly one ChatGPT host; refusing ambiguous reset.")
        host = hosts[0][0]
        tables = ("local_thread_catalog_sync_state", "local_thread_catalog_scan_checkpoints",
                  "local_thread_catalog_scan_entries", "local_thread_catalog_metadata")
        if connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (?,?,?,?)",
                              tables).fetchall():
            raise RuntimeError("Unexpected triggers on sync tables.")
        original_catalog = retained_digest(connection, [])
        other_hosts = {
            table: connection.execute(f"SELECT * FROM {table} WHERE host_id != ? ORDER BY host_id", (host,)).fetchall()
            for table in tables[:3]
        }
        old_state = connection.execute(
            "SELECT initial_build_complete,last_full_reconciled_at,watermark_updated_at "
            "FROM local_thread_catalog_sync_state WHERE host_id=?", (host,)
        ).fetchone()
        if old_state is None:
            raise RuntimeError("ChatGPT sync state missing.")
        audit.record("reconcile_plan", old_state=old_state, catalog_rows_deleted=0,
                     app_reconciliation_window_days=30)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise FileExistsError(backup)
        with closing(connect(database, "ro")) as reader, closing(sqlite3.connect(backup)) as destination:
            reader.backup(destination)
            integrity(destination)
        audit.record("backup_verified", path=str(backup), sha256=hashlib.sha256(backup.read_bytes()).hexdigest())
        # 清除舊分頁進度，讓 App 在下次啟動走現有的 full reconciliation 分支。
        for table in tables[1:3]:
            cursor = connection.execute(f"DELETE FROM {table} WHERE host_id=?", (host,))
            audit.record("sync_progress_reset_pending", table=table, affected=cursor.rowcount)
        if connection.execute(
            "UPDATE local_thread_catalog_sync_state SET initial_build_complete=0, "
            "last_full_reconciled_at=NULL,watermark_updated_at=NULL WHERE host_id=?", (host,)
        ).rowcount != 1:
            raise RuntimeError("Sync state update failed.")
        if connection.execute("UPDATE local_thread_catalog_metadata SET catalog_revision=catalog_revision+1 WHERE id=1").rowcount != 1:
            raise RuntimeError("Catalog revision update failed.")
        if retained_digest(connection, []) != original_catalog:
            raise RuntimeError("Catalog rows unexpectedly changed.")
        for table, before in other_hosts.items():
            after = connection.execute(f"SELECT * FROM {table} WHERE host_id != ? ORDER BY host_id", (host,)).fetchall()
            if after != before:
                raise RuntimeError(f"Unrelated host state changed in {table}")
        integrity(connection)
        if app_running():
            raise RuntimeError("Desktop restarted; rolling back sync reset.")
        connection.commit()
        committed = True
        audit.record("reconcile_reset_committed", catalog_rows_deleted=0,
                     catalog_preserved_sha256=original_catalog, ui_verified=False)
        audit.record("awaiting_app_reconciliation", restart_required=True,
                     app_reconciliation_window_days=30, ui_verified=False)
    except Exception:
        if not committed:
            connection.rollback()
        audit.record("reconcile_reset_failed", committed=committed, traceback=traceback.format_exc())
        raise
    finally:
        connection.close()


def main() -> int:
    parser = ui.parser(__doc__, "Inspect and clean local ChatGPT catalog rows backed by confirmed deletion evidence. Preview by default; apply waits for the desktop app to exit and creates a backup.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--verify", action="store_true", help="Read-only verification, including project rows")
    parser.add_argument("--align-recents", type=Path, help="Exact reviewed Recents plan JSON; requires --apply to modify")
    parser.add_argument("--confirmed-deleted-plan", type=Path,
                        help="User-reviewed cloud-deleted ChatGPT IDs, including project chats; requires --apply to modify")
    parser.add_argument("--scan-projects", action="store_true", help="Read-only inventory of projects and unassigned chats across all available dates")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--database", type=Path, help="Override the detected catalog database path")
    parser.add_argument("--log-root", type=Path, action="append", help="Override app log roots; repeat for multiple roots")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    ui.configure(args)
    if args.verify and args.apply:
        parser.error("--verify cannot be combined with --apply")
    if args.align_recents and args.reconcile:
        parser.error("--align-recents and --reconcile are separate operations")
    if args.confirmed_deleted_plan and (args.align_recents or args.reconcile):
        parser.error("--confirmed-deleted-plan is separate from Recents alignment and reconciliation")
    if args.scan_projects and (args.apply or args.reconcile or args.align_recents or args.confirmed_deleted_plan):
        parser.error("--scan-projects is a separate read-only operation")
    output = args.output_dir.resolve()
    run = output / "logs" / ("catalog-run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    audit = Audit(run)
    try:
        if sys.platform not in {"win32", "darwin"}:
            raise RuntimeError("Only Windows and macOS are supported.")
        database = (args.database or args.codex_home / "sqlite" / "codex-dev.db").resolve(strict=True)
        roots = args.log_root or log_roots()
        recents_plan = load_recents_plan(args.align_recents) if args.align_recents else None
        confirmed_plan = load_confirmed_deleted_plan(args.confirmed_deleted_plan) if args.confirmed_deleted_plan else None
        audit.record("started", mode="apply" if args.apply else "check", reconcile=args.reconcile, database=str(database))
        evidence = previous_evidence(output / "logs")
        evidence.update(discover_deleted(roots))
        audit.record("log_evidence", confirmed_deleted_count=len(evidence), evidence=evidence)
        with closing(connect(database, "ro")) as reader:
            validate_schema(reader)
            if args.scan_projects:
                scan_projects(reader, args.codex_home / ".codex-global-state.json", audit)
                return 0
            if recents_plan is not None:
                validate_recents_plan(reader, recents_plan)
            if confirmed_plan is not None:
                validate_confirmed_deleted_plan(reader, confirmed_plan)
            ids = ([entry["id"] for entry in confirmed_plan["entries"]] if confirmed_plan else
                   recents_plan["remove_ids"] if recents_plan else sorted(evidence))
            rows = targets(reader, ids)
            verify(reader, sorted(evidence), audit)
            diagnostics = diagnose_load_failures(reader, roots, audit)
        unresolved = [entry for entry in diagnostics["failures"]
                      if entry["source_kind"] == "chatgpt" and not entry["confirmed_deleted_catalog_target"]]
        audit.record("inspection", rows=[{"thread_id": r[1], "title": r[2]} for r in rows])
        if not args.apply:
            print(ui.text("唯讀檢查: {count} 個符合項目; 日誌: {path}", "Read-only check: {count} matching entries. Log: {path}", count=len(rows), path=audit.path))
            return 0
        if not cleanup_requires_desktop_exit(apply=args.apply, reconcile=args.reconcile, rows=rows):
            audit.record("no_op", reason="No confirmed-deleted catalog entries matched; other sidebar or loading errors may remain.",
                         catalog_rows_deleted=0, ui_verified=False)
            print(ui.text("沒有符合已確認刪除的索引, 未修改資料; 其他側邊欄或載入錯誤可能仍存在; 日誌: {path}", "No confirmed-deleted catalog entries matched. No catalog changes were made. Other sidebar or loading errors may remain. Log: {path}", path=audit.path), flush=True)
            if unresolved:
                audit.record("cloud_sidebar_review_required", thread_ids=[entry["thread_id"] for entry in unresolved],
                             reason="These cloud list items cannot be removed by deleting local catalog rows.")
                print(ui.text("仍需核對雲端側邊欄; 請透過已登入的 Codex app 封存確認過的 ID; 只重新同步無法清除這些項目", "Cloud sidebar review is still required. Use the signed-in Codex app to archive reviewed IDs; reconciliation alone cannot clear these entries."), flush=True)
                return 2
            return 0
        audit.record("waiting_for_desktop_exit", timeout_seconds=args.wait_seconds)
        print(ui.text("請完全退出 Codex/ChatGPT 並保留此外部視窗", "Quit Codex/ChatGPT completely. Keep this terminal open."), flush=True)
        deadline = time.monotonic() + args.wait_seconds
        while app_running():
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop did not exit; nothing changed.")
            time.sleep(2)
        time.sleep(2)
        # 退出後重新掃描，納入剛重現且延遲寫入日誌的其他對話。
        evidence.update(discover_deleted(roots))
        if recents_plan is None and confirmed_plan is None:
            ids = sorted(evidence)
        with closing(connect(database, "ro")) as reader:
            validate_schema(reader)
        audit.record("final_log_evidence", confirmed_deleted_count=len(evidence), evidence=evidence)
        if args.reconcile:
            reset_sync(database, audit, output / "backups" / (run.name + ".sqlite"))
        else:
            cleanup(database, audit, ids,
                    output / "backups" / (run.name + ".sqlite"), recents_plan, confirmed_plan)
        if not args.reconcile and unresolved:
            audit.record("cloud_sidebar_review_required", thread_ids=[entry["thread_id"] for entry in unresolved],
                         reason="Catalog cleanup completed, but other cloud sidebar errors remain unverified.")
            print(ui.text("索引清理已完成; 雲端側邊欄仍待核對; 日誌: {path}", "Catalog cleanup finished; cloud sidebar review remains pending. Log: {path}", path=audit.path), flush=True)
            return 2
        print(ui.text("已完成; 請重開 Codex 核對; 日誌: {path}", "Done. Reopen Codex and verify. Log: {path}", path=audit.path), flush=True)
        return 0
    except Exception:
        audit.record("failed", traceback=traceback.format_exc())
        print(traceback.format_exc(), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
