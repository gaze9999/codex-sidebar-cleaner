"""依雲端清單快照核對本機索引；唯讀、不將缺席視為已刪除。Python 3.10+。"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import traceback
from uuid import UUID

from clean_codex_catalog import Audit, connect, integrity


def compare(connection: sqlite3.Connection, snapshot: dict[str, object]) -> dict[str, object]:
    if snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported cloud snapshot schema.")
    for field in ("source", "captured_at", "account_label"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise ValueError(f"Missing {field}.")
    datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    coverage = snapshot.get("coverage")
    scopes = ("recents", "projects", "archived", "cloud_work")
    if not isinstance(coverage, dict) or any(type(coverage.get(key)) is not bool for key in scopes):
        raise ValueError("Coverage must explicitly describe recents, projects, archived and cloud_work.")
    items = snapshot.get("conversations")
    if not isinstance(items, list):
        raise ValueError("conversations must be a list.")
    cloud: dict[str, dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Invalid conversation ID.")
        key = str(UUID(item["id"]))
        if key in cloud:
            raise ValueError(f"Duplicate cloud ID: {key}")
        if not isinstance(item.get("title"), str):
            raise ValueError("Missing title.")
        for flag in ("archived", "title_verified"):
            if flag in item and type(item[flag]) is not bool:
                raise ValueError(f"{flag} must be boolean.")
        if "project_id" not in item or (item["project_id"] is not None and not isinstance(item["project_id"], str)):
            raise ValueError("project_id must explicitly be a string or null.")
        cloud[key] = item
    rows = connection.execute(
        "SELECT host_id,thread_id,display_title,source_kind,project_id,missing_candidate FROM local_thread_catalog"
    ).fetchall()
    hosts = {row[0] for row in rows if row[3] == "chatgpt"}
    if len(hosts) != 1:
        raise ValueError("Expected exactly one local ChatGPT host; cannot select an account safely.")
    matched = []
    differences = []
    local_only = []
    archived_present = []
    local_ids = set()
    visible_ids = set()
    for host, key, title, kind, project, missing in rows:
        if kind != "chatgpt":
            continue
        local_ids.add(key)
        if not missing:
            visible_ids.add(key)
        local = {"id": key, "title": title, "project_id": project, "missing_candidate": missing}
        remote = cloud.get(key)
        if remote is None:
            local_only.append(local)
        elif remote.get("archived", False):
            archived_present.append({"local": local, "cloud": remote})
        elif (remote.get("title_verified", True) and title != remote["title"]) or project != remote["project_id"] or missing:
            differences.append({"local": local, "cloud": remote})
        else:
            matched.append(key)
    active_cloud_ids = {key for key, item in cloud.items() if not item.get("archived", False)}
    active_coverage = all(coverage[key] for key in ("recents", "projects", "cloud_work"))
    return {"read_only": True, "source": snapshot["source"], "captured_at": snapshot["captured_at"],
            "account_label": snapshot["account_label"], "account_identity_independently_verified": False,
            "coverage": coverage, "reference_declares_full_coverage": all(coverage.values()),
            "cloud_count": len(cloud), "local_cloud_count": len(local_ids), "matched_ids": matched,
            "metadata_differences": differences, "local_only_unverified": local_only,
            "cloud_only": [value for key, value in cloud.items() if key not in local_ids and not value.get("archived", False)],
            "archived_present_in_catalog": archived_present,
            "archived_not_cached": [value for key, value in cloud.items() if key not in local_ids and value.get("archived", False)],
            "visible_cloud_aligned_to_snapshot": active_coverage and visible_ids == active_cloud_ids and not differences,
            "alignment_scope": "ChatGPT catalog IDs and project membership only; not the full desktop sidebar",
            "desktop_sidebar_verified": False,
            "local_tasks_may_appear_alongside_cloud_chats": True,
            "titles_not_independently_verified": sum(not item.get("title_verified", True) for item in cloud.values()),
            "excluded_local_tasks": sum(row[3] != "chatgpt" for row in rows),
            "automatic_removals": [], "fully_aligned": False,
            "limitations": ["A snapshot can be stale, incomplete or from a different account.",
                            "Missing IDs are not deletion evidence. No database changes are made.",
                            "Coverage declarations do not independently prove that all cloud pages were fetched."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud-snapshot", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sqlite/codex-dev.db")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    audit = Audit(args.output_dir / "logs" / ("cloud-compare-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    try:
        raw = args.cloud_snapshot.read_bytes()
        snapshot = json.loads(raw)
        if not isinstance(snapshot, dict):
            raise ValueError("Cloud snapshot must be an object.")
        with closing(connect(args.database, "ro")) as connection:
            integrity(connection)
            report = compare(connection, snapshot)
        report["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
        path = audit.directory / "cloud-comparison.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        audit.record("comparison_completed", report=str(path), cloud_count=report["cloud_count"],
                     local_cloud_count=report["local_cloud_count"], automatic_removals=0)
        return 0
    except Exception:
        audit.record("comparison_failed", traceback=traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
