"""Console language selection with an encoding-aware English fallback."""
from __future__ import annotations

import argparse
import os
import sys


LANGUAGES = ("zh-TW", "en")
EVENTS = {
    "started": ("開始檢查", "Inspection started"),
    "log_evidence": ("已讀取刪除證據", "Deletion evidence loaded"),
    "final_log_evidence": ("已更新退出後的刪除證據", "Deletion evidence refreshed after exit"),
    "inspection": ("已檢查符合項目", "Matching entries inspected"),
    "verification": ("已核對資料完整性與索引", "Integrity and catalog verified"),
    "load_diagnostics": ("已建立載入錯誤報告", "Load diagnostics saved"),
    "project_scan_completed": ("專案清單掃描完成", "Project inventory completed"),
    "plan": ("已建立清理計畫", "Cleanup plan saved"),
    "no_op": ("無符合項目, 未修改資料", "No matching entries; no changes made"),
    "backup_verified": ("備份已完成核對", "Backup verified"),
    "delete_pending_commit": ("索引清理等待寫入", "Catalog cleanup awaiting commit"),
    "committed": ("索引清理已寫入", "Catalog cleanup committed"),
    "completed": ("索引清理完成", "Catalog cleanup completed"),
    "cloud_sidebar_review_required": ("雲端側邊欄仍待核對", "Cloud sidebar review required"),
    "waiting_for_desktop_exit": ("等待桌面 App 完全退出", "Waiting for desktop app exit"),
    "reconcile_plan": ("已建立同步重設計畫", "Reconciliation reset planned"),
    "sync_progress_reset_pending": ("同步重設等待寫入", "Sync reset awaiting commit"),
    "reconcile_reset_committed": ("同步重設已寫入", "Sync reset committed"),
    "awaiting_app_reconciliation": ("等待 App 重新核對清單", "Waiting for app reconciliation"),
    "waiting_for_app_reconciliation": ("等待 App 重新核對清單", "Waiting for app reconciliation"),
    "app_reconciliation_observed": ("已確認 App 完成清單核對", "App reconciliation observed"),
    "sidebar_reference_candidates": ("已列出待核對的專案參照", "Sidebar reference candidates saved"),
    "sidebar_reference_plan": ("已建立參照清理計畫", "Sidebar reference plan saved"),
    "sidebar_reference_backup": ("側邊欄設定備份完成", "Sidebar state backed up"),
    "sidebar_references_removed": ("已清除選定專案參照", "Selected project references removed"),
    "sidebar_reference_inspection": ("已檢查側邊欄專案參照", "Sidebar references inspected"),
    "sidebar_reference_inspection_unavailable": ("無法確認參照資料格式", "Sidebar reference schema unavailable"),
    "cloud_sidebar_archive_status": ("已核對雲端封存進度", "Cloud archive status checked"),
    "screenshot_cleanup_plan": ("截圖清理計畫已儲存", "Screenshot cleanup plan saved"),
    "stage_started": ("開始執行流程步驟", "Workflow stage started"),
    "stage_finished": ("流程步驟已結束", "Workflow stage finished"),
    "workflow_stopped": ("流程已停止, 請查看記錄檔", "Workflow stopped; inspect the log"),
    "workflow_completed": ("指定流程已完成", "Requested workflow completed"),
    "organization_plan": ("已建立本機對話整理計畫", "Local organization planned"),
    "organization_no_changes": ("沒有需要整理的本機對話", "No local organization changes needed"),
    "organization_started": ("開始整理本機對話", "Local organization started"),
    "organization_verified": ("本機對話整理核對完成", "Local organization verified"),
    "section_create_requested": ("正在建立側邊欄區段", "Creating sidebar section"),
    "section_created": ("側邊欄區段已建立", "Sidebar section created"),
    "move_requested": ("正在移動本機對話", "Moving local conversation"),
    "move_completed": ("本機對話已移動", "Local conversation moved"),
    "move_skipped_changed_assignment": ("分類已變更, 已跳過該對話", "Assignment changed; conversation skipped"),
    "initial_verification_started": ("開始初始檢查", "Initial verification started"),
    "initial_verification_finished": ("初始檢查已結束", "Initial verification finished"),
    "archive_deletion_plan": ("已建立本機封存刪除計畫", "Local archive deletion planned"),
    "archive_backup_completed": ("本機封存刪除備份完成", "Local archive backup completed"),
    "archive_delete_requested": ("正在刪除選定本機封存對話", "Deleting selected local archive"),
    "archive_delete_verified": ("已核對本機對話刪除結果", "Local deletion verified"),
    "archive_deletion_verified": ("本機封存刪除核對完成", "Local archive deletion verified"),
    "interactive_archive_preview": ("已預覽本機封存對話", "Local archives previewed"),
    "archive_deletion_cancelled": ("已取消刪除", "Deletion cancelled"),
    "archive_recovery_source_selected": ("已選擇封存對話來源", "Archive source selected"),
    "archive_stage_finished": ("封存處理階段已結束", "Archive stage finished"),
    "cloud_archive_manager_handoff": ("雲端刪除待網頁確認", "Cloud deletion awaits web confirmation"),
    "cloud_archive_cache_preview": ("已預覽雲端殘留索引", "Cloud cache selection previewed"),
    "cloud_deletion_confirmed_by_user": ("已記錄使用者確認的雲端刪除結果", "User-confirmed cloud deletion recorded"),
    "confirmed_recents_alignment": ("已核對最近項目清理範圍", "Recents scope reviewed"),
    "user_confirmed_cloud_deletion": ("已讀取確認過的雲端刪除清單", "Reviewed cloud deletion plan loaded"),
}


def _supports_chinese() -> bool:
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if encoding:
            try:
                "繁體中文清理封存對話請選擇".encode(encoding)
            except (LookupError, UnicodeError):
                return False
    return True


def language() -> str:
    value = os.environ.get("SIDEBAR_CLEANER_LANG", "zh-TW")
    if value not in LANGUAGES:
        value = "zh-TW"
    return "en" if value == "zh-TW" and not _supports_chinese() else value


def text(chinese: str, english: str, **fields: object) -> str:
    return (chinese if language() == "zh-TW" else english).format(**fields)


def event_message(event: str) -> str:
    if event.endswith("failed"):
        return text("執行失敗, 請查看記錄檔中的詳細原因", "Operation failed; inspect the log for details")
    return text(*EVENTS.get(event, ("已記錄執行進度", "Progress recorded")))


def parser(chinese: str, english: str) -> argparse.ArgumentParser:
    # Select before parsing so --help also uses the requested language.
    for index, value in enumerate(sys.argv[1:], start=1):
        if value == "--lang" and index + 1 < len(sys.argv) and sys.argv[index + 1] in LANGUAGES:
            os.environ["SIDEBAR_CLEANER_LANG"] = sys.argv[index + 1]
        elif value.startswith("--lang=") and value[7:] in LANGUAGES:
            os.environ["SIDEBAR_CLEANER_LANG"] = value[7:]
    result = argparse.ArgumentParser(description=text(chinese, english))
    result.add_argument("--lang", choices=LANGUAGES, default=language(),
                        help=text("介面語言, 預設繁體中文", "Display language (default: Traditional Chinese)"))
    return result


def configure(args: argparse.Namespace) -> None:
    os.environ["SIDEBAR_CLEANER_LANG"] = args.lang
    args.lang = language()
    os.environ["SIDEBAR_CLEANER_LANG"] = args.lang
    if not _supports_chinese():
        # English messages may still include user-supplied Chinese paths/titles.
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure:
                reconfigure(errors="backslashreplace")
