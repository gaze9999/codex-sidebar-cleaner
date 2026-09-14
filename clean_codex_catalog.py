"""依全部桌面日誌清理已確認 conversation_deleted 的 ChatGPT 側欄索引。

python clean_codex_catalog.py          唯讀檢查並留下 log
python clean_codex_catalog.py --apply  等待桌面退出，備份後以交易清理
python clean_codex_catalog.py --apply --reconcile  重設雲端清單同步，交由 App 核對最近約 30 天

Python 3.10+ / Windows。紀錄寫入 logs/catalog-run-*，備份寫入 backups/。
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
import time
import traceback
from typing import TypedDict, cast


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
    local = Path(os.environ["LOCALAPPDATA"])
    roots = [p / "LocalCache" / "Local" / "Codex" / "Logs"
             for p in (local / "Packages").glob("OpenAI.Codex_*")]
    roots.append(local / "Codex" / "Logs")
    return roots


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
    print(f"Known deleted entries remaining: {len(remaining)}. See log for project and sync counts.", flush=True)


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
        print(f"{entry['time']} {event}", flush=True)


def app_running() -> bool:
    result = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True,
        check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    rows = csv.reader(result.stdout.decode("utf-8", errors="replace").splitlines())
    return any(row and row[0].lower() in {"chatgpt.exe", "codex-desktop.exe"} for row in rows)


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
            recents_plan: RecentsPlan | None = None) -> None:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--verify", action="store_true", help="Read-only verification, including project rows")
    parser.add_argument("--align-recents", type=Path, help="Exact reviewed Recents plan JSON; requires --apply to modify")
    parser.add_argument("--scan-projects", action="store_true", help="Read-only inventory of projects and unassigned chats across all available dates")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--database", type=Path, help="Override the detected catalog database path")
    parser.add_argument("--log-root", type=Path, action="append", help="Override app log roots; repeat for multiple roots")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.verify and args.apply:
        parser.error("--verify cannot be combined with --apply")
    if args.align_recents and args.reconcile:
        parser.error("--align-recents and --reconcile are separate operations")
    if args.scan_projects and (args.apply or args.reconcile or args.align_recents):
        parser.error("--scan-projects is a separate read-only operation")
    output = args.output_dir.resolve()
    run = output / "logs" / ("catalog-run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    audit = Audit(run)
    try:
        if os.name != "nt":
            raise RuntimeError("Windows is required.")
        database = (args.database or args.codex_home / "sqlite" / "codex-dev.db").resolve(strict=True)
        roots = args.log_root or log_roots()
        recents_plan = load_recents_plan(args.align_recents) if args.align_recents else None
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
            rows = targets(reader, recents_plan["remove_ids"] if recents_plan else sorted(evidence))
            verify(reader, sorted(evidence), audit)
        audit.record("inspection", rows=[{"thread_id": r[1], "title": r[2]} for r in rows])
        if not args.apply:
            print(f"Read-only check: {len(rows)} matching entries. Log: {audit.path}")
            return 0
        audit.record("waiting_for_desktop_exit", timeout_seconds=args.wait_seconds)
        print("Exit Codex/ChatGPT from the system tray. Keep THIS console open.", flush=True)
        deadline = time.monotonic() + args.wait_seconds
        while app_running():
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop did not exit; nothing changed.")
            time.sleep(2)
        time.sleep(2)
        # 退出後重新掃描，納入剛重現且延遲寫入日誌的其他對話。
        evidence.update(discover_deleted(roots))
        with closing(connect(database, "ro")) as reader:
            validate_schema(reader)
        audit.record("final_log_evidence", confirmed_deleted_count=len(evidence), evidence=evidence)
        if args.reconcile:
            reset_sync(database, audit, output / "backups" / (run.name + ".sqlite"))
        else:
            cleanup(database, audit, recents_plan["remove_ids"] if recents_plan else sorted(evidence),
                    output / "backups" / (run.name + ".sqlite"), recents_plan)
        print(f"Done. Reopen Codex and verify. Log: {audit.path}", flush=True)
        return 0
    except Exception:
        audit.record("failed", traceback=traceback.format_exc())
        print(traceback.format_exc(), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
