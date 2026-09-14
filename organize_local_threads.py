"""將尚未分類的本機 Codex 任務移至獨立區段。預設預覽，加 --apply 執行。

保留專案、釘選與其他區段的分類；不刪除對話。需要相容的 Codex CLI。
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback

from clean_codex_catalog import Audit, connect


class AppServer:
    def __init__(self, executable: str, home: Path) -> None:
        self.sequence = 0
        self.messages: queue.Queue[str | None] = queue.Queue()
        environment = dict(os.environ, CODEX_HOME=str(home.resolve()))
        self.process = subprocess.Popen(
            [executable, "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(target=self._read, daemon=True).start()
        try:
            result = self.call("initialize", {"clientInfo": {"name": "sidebar-cleaner", "version": "2"},
                                              "capabilities": {"experimentalApi": True}})
            actual = result.get("codexHome")
            if not isinstance(actual, str) or Path(actual).resolve() != home.resolve():
                raise RuntimeError("App-server selected a different Codex home; nothing moved.")
        except Exception:
            self.close()
            raise

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.messages.put(line)
        finally:
            self.messages.put(None)

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.sequence += 1
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"id": self.sequence, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                line = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise TimeoutError(f"No response to {method}; inspect current state before retrying.") from error
            if line is None:
                raise RuntimeError(f"App-server exited during {method}.")
            reply = json.loads(line)
            if reply.get("id") != self.sequence:
                continue
            if "error" in reply:
                raise RuntimeError(f"{method}: {reply['error']}")
            result = reply.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"Unsupported response to {method}.")
            return result

    def listing(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
        cursor = None
        seen = set()
        items = []
        while True:
            page = self.call(method, dict(params, cursor=cursor, limit=100))
            data = page.get("data")
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise RuntimeError(f"Unsupported list response: {method}")
            items.extend(data)
            cursor = page.get("nextCursor")
            if cursor is None:
                return items
            if not isinstance(cursor, str) or cursor in seen:
                raise RuntimeError("Invalid or repeated pagination cursor.")
            seen.add(cursor)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


def eligible_catalog(home: Path) -> dict[str, str]:
    # 舊版桌面專案指派尚未移入 app-server；兩個來源都需保護。
    state = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Unsupported desktop state.")
    assignments = state.get("thread-project-assignments", {})
    if not isinstance(assignments, dict):
        raise ValueError("Unsupported project assignments.")
    with closing(connect(home / "sqlite/codex-dev.db", "ro")) as reader:
        rows = reader.execute(
            "SELECT thread_id,display_title FROM local_thread_catalog "
            "WHERE host_id='local' AND source_kind IN ('vscode','cli','appServer') "
            "AND project_id IS NULL AND missing_candidate=0"
        ).fetchall()
    result = {}
    for key, title in rows:
        # 任何明確指派都保留，即使專案清單暫時未載入。
        if key in assignments:
            continue
        result[key] = title
    return result


def select_candidates(rows: list[dict[str, object]], eligible: dict[str, str]) -> list[dict[str, object]]:
    result = []
    seen = set()
    for row in rows:
        if "projectId" not in row or not isinstance(row.get("id"), str):
            raise ValueError("Unsupported thread schema; cannot verify project membership.")
        key = row["id"]
        if key in seen:
            raise ValueError("Duplicate thread ID in server listing.")
        seen.add(key)
        if key in eligible and row["projectId"] is None and row.get("source") in ("vscode", "cli", "appServer"):
            result.append({"id": key, "title": eligible[key]})
    return result


def organize(server: AppServer, home: Path, name: str, apply: bool, audit: Audit) -> None:
    sections = server.listing("threadSection/list", {})
    matching = [section for section in sections if section.get("name") == name]
    if len(matching) > 1:
        raise ValueError("Multiple sections have this name. Rename one before retrying.")
    target = matching[0]["id"] if matching else None
    if target is not None and not isinstance(target, str):
        raise ValueError("Invalid section ID.")
    filters: dict[str, object] = {"archived": False, "sectionId": None, "projectId": None,
                                  "useStateDbOnly": True, "sourceKinds": ["vscode", "cli", "appServer"]}
    candidates = select_candidates(server.listing("thread/list", filters), eligible_catalog(home))
    audit.record("organization_plan", section_name=name, existing_section_id=target, candidates=candidates,
                 preserves_projects=True, preserves_other_sections=True, deletes_content=False)
    if not apply or not candidates:
        audit.record("organization_no_changes", preview=not apply, candidate_count=len(candidates))
        return
    # 保存區段快照與原始分類；每筆提交立即寫 log，部分失敗可安全重跑。
    (audit.directory / "before-sections.json").write_text(json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8")
    if target is None:
        audit.record("section_create_requested", name=name)
        created = server.call("threadSection/create", {"name": name}).get("section")
        if not isinstance(created, dict) or not isinstance(created.get("id"), str):
            raise RuntimeError("Unsupported create response. Inspect sections before retrying.")
        target = created["id"]
        audit.record("section_created", section_id=target)
    moved = []
    for item in candidates:
        # 每次搬移前重新核對，避免覆蓋執行中使用者新增的分類。
        current = select_candidates(server.listing("thread/list", filters), eligible_catalog(home))
        if item["id"] not in {row["id"] for row in current}:
            audit.record("move_skipped_changed_assignment", thread_id=item["id"])
            continue
        audit.record("move_requested", thread_id=item["id"], previous_section_id=None, section_id=target)
        server.call("thread/section/move", {"threadId": item["id"], "sectionId": target})
        moved.append(item["id"])
        audit.record("move_completed", thread_id=item["id"], section_id=target)
    members = server.listing("thread/list", {"sectionId": target, "archived": False, "useStateDbOnly": True})
    missing = set(moved) - {item["id"] for item in members}
    if missing:
        raise RuntimeError(f"Moved thread membership did not verify: {sorted(missing)}")
    audit.record("organization_verified", moved_count=len(moved), section_id=target,
                 desktop_ui_verified=False, restart_may_be_needed=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--section-name", default="本機 Codex")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--codex", help="Path to the installed Codex executable")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    audit = Audit(args.output_dir / "logs" / ("local-organize-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    try:
        if not args.section_name.strip():
            raise ValueError("Section name cannot be empty.")
        home = args.codex_home.resolve(strict=True)
        audit.record("initial_verification_started")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-u", str(Path(__file__).with_name("clean_codex_catalog.py")),
             "--verify", "--codex-home", str(home), "--output-dir", str(args.output_dir.resolve())],
            check=False,
        )
        audit.record("initial_verification_finished", exit_code=result.returncode)
        if result.returncode:
            return result.returncode
        eligible_catalog(home)
        executable = args.codex or shutil.which("codex")
        if executable is None:
            raise FileNotFoundError("Codex CLI not found. Install Codex CLI or pass --codex with its path.")
        audit.record("organization_started", apply=args.apply, codex_home=str(home))
        with closing(AppServer(executable, home)) as server:
            organize(server, home, args.section_name.strip(), args.apply, audit)
        print(f"Done. Log: {audit.path}")
        return 0
    except Exception:
        audit.record("organization_failed", traceback=traceback.format_exc(),
                     limitation="Earlier successful moves, if any, remain committed; rerun to resume.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
