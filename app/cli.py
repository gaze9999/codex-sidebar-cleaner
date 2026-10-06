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
    ("main", "1"): ("maintain_sidebar.py", "--apply", "--review-sidebar-references"),
    ("main", "2"): ("clean_archived_conversations.py", "--interactive"),
    ("advanced", "1"): ("maintain_sidebar.py", "--apply", "--interactive-sidebar-plan"),
    ("advanced", "2"): ("clean_sidebar_references.py", "--interactive"),
    ("advanced", "3"): ("plan_sidebar_cleanup.py", "--interactive"),
    ("advanced", "4"): ("organize_local_threads.py", "--apply"),
    ("advanced", "5"): ("clean_codex_catalog.py", "--apply"),
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
            print(ui.text("1. 修復側邊欄\n2. 清理封存對話\n3. 進階工具",
                          "1. Fix sidebar\n2. Clean archives\n3. More tools"))
            print(f"\n{switch}\n" + ui.text("0. 離開", "0. Exit"))
            prompt = ui.text("請選擇 [1-3,L,0]: ", "Choose [1-3,L,0]: ")
        else:
            print(ui.text("進階工具\n\n1. 執行已核對的修正清單\n2. 移除舊專案參照\n"
                          "3. 建立封存清單\n4. 整理本機對話\n5. 清除已刪除對話的快取",
                          "More tools\n\n1. Apply a reviewed plan\n2. Remove old project links\n"
                          "3. Prepare an archive plan\n4. Organize local chats\n5. Clean deleted chat cache"))
            print(f"\n{switch}\n" + ui.text("0. 返回", "0. Back"))
            prompt = ui.text("請選擇 [1-5,L,0]: ", "Choose [1-5,L,0]: ")
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
        if menu == "main" and choice == "3":
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
