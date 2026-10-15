"""Shared Windows/macOS menu; data operations remain in the existing CLI tools."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import cleaner_language as ui
from runtime_paths import APP, ROOT as OUTPUT_ROOT, tool_command


ROOT = APP
ROUTES = {
    ("main", "1"): ("maintain_sidebar.py",),
    ("main", "3"): ("clean_archived_conversations.py", "--interactive", "--source", "cloud"),
    ("main", "4"): ("clean_archived_conversations.py", "--interactive", "--source", "local"),
    ("advanced", "1"): ("clean_sidebar_references.py", "--interactive"),
    ("advanced", "2"): ("maintain_sidebar.py", "--apply", "--interactive-sidebar-plan"),
    ("advanced", "3"): ("organize_local_threads.py", "--apply"),
    ("advanced", "4"): ("plan_sidebar_cleanup.py", "--interactive"),
}


def route_command(menu: str, choice: str, language: str) -> list[str]:
    script, *flags = ROUTES[(menu, choice)]
    return tool_command(ROOT / script, "--lang", language, *flags)


def main() -> int:
    parser = ui.parser("啟動側邊欄維護選單", "Open the sidebar maintenance menu")
    parser.add_argument("language", nargs="?", choices=ui.LANGUAGES,
                        help=ui.text("啟動時使用的語言", "Startup language"))
    args = parser.parse_args()
    args.lang = args.language or args.lang
    requested = args.lang
    ui.configure(args)
    menu = "main"
    while True:
        if sys.stdout.isatty():
            if os.name == "nt":
                os.system("cls")
            elif shutil.which("clear"):
                os.system("clear")
        print("Codex Sidebar Cleaner\n")
        if requested == "zh-TW" and ui.language() == "en":
            print("Chinese output is unavailable. Using English.\n")
        switch = "L. English" if ui.language() == "zh-TW" else "L. Chinese"
        if menu == "main":
            print(ui.text(
                "1. 檢查側邊欄 (唯讀)\n   掃描索引、專案參照與載入錯誤\n"
                "2. 整理側邊欄\n   移除指定參照、套用計畫或整理本機分類\n"
                "3. 管理 ChatGPT 雲端封存\n   開啟網頁管理, 或清理已確認刪除的本機索引\n"
                "4. 永久刪除本機封存對話\n   依專案預覽, 備份後輸入 DELETE 確認",
                "1. Inspect sidebar (read-only)\n   Scan catalog, project links and loading errors\n"
                "2. Organize sidebar\n   Remove selected links, apply a plan or group local chats\n"
                "3. Manage ChatGPT archives\n   Open web management or clean confirmed-deleted local cache\n"
                "4. Permanently delete local archived chats\n   Preview by project, back up and confirm with DELETE"))
            print(f"\n{switch}\n" + ui.text("0. 離開", "0. Exit"))
            prompt = ui.text("請選擇 [1-4,L,0]: ", "Choose [1-4,L,0]: ")
        else:
            print(ui.text(
                "整理側邊欄\n\n1. 選擇並移除舊專案參照\n   只修改本機側邊欄分類\n"
                "2. 套用已核對的參照清理計畫\n   選擇計畫檔後執行\n"
                "3. 將未分類本機對話集中到 Local Codex\n   保留既有分類與對話內容\n"
                "4. 建立雲端封存請求清單\n   產生計畫, 不直接封存",
                "Organize sidebar\n\n1. Select and remove old project links\n   Update local sidebar sections only\n"
                "2. Apply a reviewed reference cleanup plan\n   Select a plan file to execute\n"
                "3. Group unassigned local chats under Local Codex\n   Preserve existing sections and content\n"
                "4. Prepare cloud archive requests\n   Create a plan without archiving chats"))
            print(f"\n{switch}\n" + ui.text("0. 返回主選單", "0. Back to main menu"))
            prompt = ui.text("請選擇 [1-4,L,0]: ", "Choose [1-4,L,0]: ")
        try:
            choice = input(prompt).strip().upper()
        except EOFError:
            return 0
        if choice == "L":
            args.lang = "en" if ui.language() == "zh-TW" else "zh-TW"
            requested = args.lang
            ui.configure(args)
            continue
        if choice == "0":
            if menu == "main":
                return 0
            menu = "main"
            continue
        if menu == "main" and choice == "2":
            menu = "advanced"
            continue
        route = ROUTES.get((menu, choice))
        if route is None:
            print(ui.text("選項無效, 請重新選擇", "Invalid choice. Try again."))
            continue
        result = subprocess.run(route_command(menu, choice, ui.language()), check=False)
        status = result.returncode
        if status == 0:
            print(ui.text("操作已結束", "Finished"))
        elif status == 2:
            print(ui.text("仍有項目待處理, 請依上方提示完成", "Action needed. See the message above."))
        else:
            print(ui.text("流程已停止, 結束代碼: {status}", "Stopped. Exit code: {status}", status=status))
        if status != 0:
            print(ui.text("記錄檔: {path}", "Logs: {path}", path=OUTPUT_ROOT / "logs"))
        try:
            input(ui.text("按 Enter 關閉: ", "Press Enter to close: "))
        except EOFError:
            pass
        return status


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
