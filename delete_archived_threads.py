"""Preview or delete selected local archives, with backups and dependency checks.

Uses thread/delete for local Codex chats. Does not delete ChatGPT cloud chats.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import traceback

from clean_codex_catalog import Audit, connect
from organize_local_threads import AppServer


def catalog(home: Path) -> tuple[dict[str, dict], dict[str, set[str]]]:
    with closing(connect(home / "state_5.sqlite", "ro")) as db:
        db.row_factory = sqlite3.Row
        required = {"id", "rollout_path", "archived", "source", "thread_source", "cwd"}
        if not required <= {row[1] for row in db.execute("PRAGMA table_info(threads)")}:
            raise ValueError("Unsupported local thread schema.")
        rows = {row["id"]: dict(row) for row in db.execute("SELECT * FROM threads")}
        spawned: dict[str, set[str]] = {}
        for parent, child in db.execute("SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges"):
            spawned.setdefault(parent, set()).add(child)
    return rows, spawned


def references(rows: dict[str, dict], home: Path) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for key, row in rows.items():
        path = Path(row["rollout_path"])
        if not path.is_file():
            continue
        # Session metadata is at the start; history references must never be inferred from chat text.
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                if item.get("type") == "turn_context":
                    break
                if item.get("type") != "session_meta":
                    continue
                meta = item["payload"]
                if meta.get("id") != key:
                    raise ValueError(f"Rollout identity mismatch: {key}")
                base = meta.get("history_base") or {}
                for parent in (meta.get("forked_from_id"), base.get("thread_id")):
                    if isinstance(parent, str) and parent != key:
                        refs.setdefault(parent, set()).add(key)
                break
    return refs


def plan_deletion(rows: dict[str, dict], spawned: dict[str, set[str]], refs: dict[str, set[str]],
                  ids: list[str], include_dependencies: bool, allow_missing_rollouts: bool = False) -> dict:
    if not ids or any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)):
        raise ValueError("Specify unique local thread IDs; empty selection is not allowed.")
    missing = set(ids) - rows.keys()
    if missing:
        raise ValueError(f"Selected IDs absent from local state: {sorted(missing)}")
    affected = set(ids)
    pending = list(ids)
    while pending:
        parent = pending.pop()
        for child in spawned.get(parent, set()) | refs.get(parent, set()):
            if child not in rows:
                raise ValueError(f"Dependency missing from local state: {child}")
            if child not in affected:
                if child in refs.get(parent, set()) and not include_dependencies:
                    raise ValueError(f"Archived fork requires explicit --include-archived-dependencies: {child}")
                affected.add(child)
                pending.append(child)
    missing_rollouts = []
    for key in sorted(affected):
        row = rows[key]
        source = row["source"]
        if source not in ("vscode", "cli", "appServer"):
            try:
                source = json.loads(source)
            except (ValueError, TypeError):
                source = None
        local = source in ("vscode", "cli", "appServer") or (
            isinstance(source, dict) and "subagent" in source and
            row["thread_source"] in ("subagent", "agent_forked_thread"))
        if row["archived"] != 1 or not local:
            raise ValueError(f"Active or unsupported thread would be deleted: {key}")
        raw_path = row["rollout_path"]
        if not isinstance(raw_path, str) or not raw_path or not Path(raw_path).is_absolute():
            raise ValueError(f"Unsupported rollout path: {key}")
        path = Path(raw_path)
        if path.exists() and not path.is_file():
            raise ValueError(f"Rollout is not a regular file: {key}")
        if not path.is_file():
            if not allow_missing_rollouts:
                raise ValueError(f"Rollout missing; review with --allow-missing-rollouts: {key}")
            missing_rollouts.append(key)
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("Cycle in fork/spawn references.")
        if key in visited:
            return
        visiting.add(key)
        for child in sorted(spawned.get(key, set()) | refs.get(key, set())):
            if child in affected:
                visit(child)
        visiting.remove(key)
        visited.add(key)
        order.append(key)
    for key in ids:
        visit(key)
    return {"selected_ids": ids, "affected_ids": sorted(affected), "delete_order": order,
            "dependencies": sorted(affected - set(ids)), "missing_rollout_ids": missing_rollouts,
            "cloud_deleted": False}


def snapshot(rows: dict[str, dict], plan: dict) -> dict:
    result = {}
    for key in plan["affected_ids"]:
        try:
            stat = Path(rows[key]["rollout_path"]).stat()
        except FileNotFoundError:
            stat = None
        result[key] = {"row": rows[key], "rollout_exists": stat is not None,
                       "size": stat.st_size if stat else None,
                       "mtime_ns": stat.st_mtime_ns if stat else None}
    return result


def backup(home: Path, directory: Path, rows: dict[str, dict], plan: dict) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    databases = [home / "state_5.sqlite", home / "sqlite/codex-dev.db"]
    for pattern in ("thread_history_*.sqlite", "memories_*.sqlite", "goals_*.sqlite", "queue_*.sqlite"):
        databases.extend(sorted(home.glob(pattern)))
    for path in databases:
        relative = path.relative_to(home)
        if not path.is_file():
            continue
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path, "ro")) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeError(f"Backup integrity failed: {relative}")
    for key in plan["affected_ids"]:
        source = Path(rows[key]["rollout_path"]).resolve()
        if not source.is_relative_to(home.resolve()):
            raise ValueError(f"Rollout outside Codex home: {key}")
        if key in plan["missing_rollout_ids"]:
            if source.exists():
                raise RuntimeError(f"Missing rollout reappeared before backup: {key}")
            continue
        target = directory / source.relative_to(home.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if source.stat().st_size != target.stat().st_size:
            raise RuntimeError(f"Backup size mismatch: {key}")
    state = home / ".codex-global-state.json"
    if state.is_file():
        shutil.copy2(state, directory / state.name)
    (directory / "manifest.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_archived(server: AppServer | None, home: Path, ids: list[str], include_dependencies: bool,
                    apply: bool, audit: Audit, backup_dir: Path, reviewed_plan: dict | None = None,
                    allow_missing_rollouts: bool = False) -> dict:
    rows, spawned = catalog(home)
    refs = references(rows, home)
    plan = plan_deletion(rows, spawned, refs, ids, include_dependencies, allow_missing_rollouts)
    if reviewed_plan is not None and reviewed_plan != plan:
        raise RuntimeError("Scope changed since confirmation; preview again.")
    before = snapshot(rows, plan)
    audit.record("archive_deletion_plan", **plan, preview=not apply)
    (audit.directory / "deletion-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if not apply:
        return plan
    if server is None:
        raise ValueError("An initialized app-server is required to delete.")
    backup(home, backup_dir, rows, plan)
    audit.record("archive_backup_completed", directory=str(backup_dir))
    current, current_spawned = catalog(home)
    fresh = plan_deletion(current, current_spawned, references(current, home), ids, include_dependencies,
                          allow_missing_rollouts)
    if fresh != plan or snapshot(current, fresh) != before:
        raise RuntimeError("Thread state changed while backing up; preview again before retrying.")
    completed = []
    for key in plan["delete_order"]:
        now, now_spawned = catalog(home)
        if key not in now:
            raise RuntimeError(f"Thread disappeared before deletion: {key}; inspect before retrying.")
        if now[key]["archived"] != 1:
            raise RuntimeError(f"Thread was unarchived: {key}; deletion stopped.")
        if snapshot(now, {"affected_ids": [key]})[key] != before[key]:
            raise RuntimeError(f"Thread changed after backup: {key}; deletion stopped.")
        # Recheck the entire remaining cascade, including forks created after the backup.
        remaining = plan_deletion(now, now_spawned, references(now, home), [key], True, allow_missing_rollouts)
        if not set(remaining["affected_ids"]) <= set(plan["affected_ids"]):
            raise RuntimeError("New dependency outside backed-up scope; deletion stopped.")
        audit.record("archive_delete_requested", thread_id=key)
        server.call("thread/delete", {"threadId": key})
        checked, _ = catalog(home)
        if key in checked or Path(rows[key]["rollout_path"]).exists():
            raise RuntimeError(f"Deletion did not verify: {key}")
        completed.append(key)
        audit.record("archive_delete_verified", thread_id=key)
    after, _ = catalog(home)
    if set(plan["affected_ids"]) & after.keys():
        raise RuntimeError("Some planned threads remain.")
    unrelated_missing = set(rows) - set(plan["affected_ids"]) - set(after)
    if unrelated_missing:
        raise RuntimeError(f"Unrelated threads disappeared: {sorted(unrelated_missing)}")
    audit.record("archive_deletion_verified", selected_count=len(ids), deleted_count=len(completed),
                 unrelated_threads_preserved=True, cloud_deleted=False)
    return plan


def main() -> int:
    import cleaner_language as ui
    parser = ui.parser("預覽並刪除明確選定的本機封存對話; 執行前會備份與檢查相依項目", __doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--ids-file", type=Path, help="JSON array of exact local thread IDs")
    selection.add_argument("--interactive", action="store_true", help="Preview selected project archives, then confirm")
    parser.add_argument("--include-archived-dependencies", action="store_true")
    parser.add_argument("--allow-missing-rollouts", action="store_true",
                        help="Review archived local metadata even when its rollout is already missing")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--codex", help="Installed Codex executable")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    ui.configure(args)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    audit = Audit(args.output_dir / "logs" / ("archive-delete-" + stamp))
    try:
        reviewed_plan = None
        home = args.codex_home.resolve(strict=True)
        if args.interactive:
            cwd = input(ui.text("專案資料夾 (空白取消): ", "Project folder (empty to cancel): ")).strip().strip('"')
            if not cwd:
                return 0
            project = Path(cwd).resolve(strict=True)
            rows, spawned = catalog(home)
            ids = [key for key, row in rows.items() if row["archived"] == 1 and
                   row["thread_source"] == "user" and Path(row["cwd"]).resolve() == project]
            args.include_archived_dependencies = True
            if not ids:
                print(ui.text("此專案沒有本機封存對話", "This project has no local archived conversations"))
                return 0
            plan = plan_deletion(rows, spawned, references(rows, home), ids, True, args.allow_missing_rollouts)
            for key in plan["delete_order"]:
                print(f"{key}  {(rows[key].get('name') or rows[key].get('title') or '(hidden)')[:100]}")
            print(ui.text("已選 {count} 個; 包含封存的分支與子對話共 {total} 個", "Selected: {count}; total including archived forks/children: {total}", count=len(ids), total=len(plan['affected_ids'])))
            if plan["missing_rollout_ids"]:
                print(ui.text("其中 {count} 個內容檔案已遺失; 將備份殘留資料後刪除, 遺失的內容無法備份",
                              "{count} rollout files are already missing. Remaining metadata will be backed up; missing content cannot be backed up",
                              count=len(plan["missing_rollout_ids"])))
            audit.record("interactive_archive_preview", **plan)
            if input(ui.text("備份後永久刪除, 無法在 App 還原; 輸入 DELETE 確認: ", "Delete permanently after backup. Cannot be undone in the app. Type DELETE to confirm: ")).strip() != "DELETE":
                audit.record("archive_deletion_cancelled")
                return 0
            args.apply = True
            reviewed_plan = plan
        else:
            ids = json.loads(args.ids_file.read_text(encoding="utf-8-sig"))
        if not isinstance(ids, list):
            raise ValueError("IDs file must be a JSON array.")
        if not args.apply:
            delete_archived(None, home, ids, args.include_archived_dependencies, False, audit,
                            args.output_dir / "backups" / ("archive-delete-" + stamp),
                            allow_missing_rollouts=args.allow_missing_rollouts)
            print(ui.text("預覽完成; 日誌: {path}", "Preview complete. Log: {path}", path=audit.path))
            return 0
        executable = args.codex or shutil.which("codex")
        if executable is None:
            raise FileNotFoundError("Codex CLI not found; pass --codex.")
        with closing(AppServer(executable, home)) as server:
            delete_archived(server, home, ids, args.include_archived_dependencies, args.apply, audit,
                            args.output_dir / "backups" / ("archive-delete-" + stamp), reviewed_plan,
                            args.allow_missing_rollouts)
        print(ui.text("已完成; 日誌: {path}", "Done. Log: {path}", path=audit.path))
        return 0
    except Exception:
        audit.record("archive_deletion_failed", traceback=traceback.format_exc(),
                     limitation="Successful deletions remain committed; inspect log before retrying.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
