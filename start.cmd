@echo off
setlocal
chcp 65001 >nul
if "%~1"=="en" set "SIDEBAR_CLEANER_LANG=en"
if not "%SIDEBAR_CLEANER_LANG%"=="en" set "SIDEBAR_CLEANER_LANG=zh-TW"
title Codex Sidebar Cleaner
:menu
cls
echo Codex Sidebar Cleaner
echo.
if "%SIDEBAR_CLEANER_LANG%"=="en" goto english
echo 1. 修復側邊欄
echo 2. 清理封存對話
echo 3. 進階工具
echo.
echo L. English
echo 0. 離開
choice /c 123L0 /n /m "請選擇 [1-3,L,0]: "
goto selected
:english
echo 1. Repair sidebar
echo 2. Clean archived conversations
echo 3. Advanced tools
echo.
echo L. 繁體中文
echo 0. Exit
choice /c 123L0 /n /m "Choose [1-3,L,0]: "
:selected
if errorlevel 5 exit /b 0
if errorlevel 4 goto language
if errorlevel 3 goto advanced
if errorlevel 2 goto archives
set "cleaner_script=maintain_sidebar.py"
set "cleaner_args=--apply --review-sidebar-references"
goto run
:advanced
cls
if "%SIDEBAR_CLEANER_LANG%"=="en" goto advanced_en
echo 進階工具
echo.
echo 1. 執行已核對的修正清單
echo 2. 移除舊專案參照
echo 3. 依截圖建立封存清單
echo 4. 整理本機對話
echo 5. 只清除已刪除的索引
echo.
echo 0. 返回
choice /c 123450 /n /m "請選擇 [1-5,0]: "
goto advanced_selected
:advanced_en
echo Advanced tools
echo.
echo 1. Run a reviewed repair plan
echo 2. Remove stale project references
echo 3. Prepare an archive plan from screenshots
echo 4. Organize local conversations
echo 5. Clean confirmed deleted entries only
echo.
echo 0. Back
choice /c 123450 /n /m "Choose [1-5,0]: "
:advanced_selected
if errorlevel 6 goto menu
if errorlevel 5 goto catalog
if errorlevel 4 goto organize
if errorlevel 3 goto screenshots
if errorlevel 2 goto references
set "cleaner_script=maintain_sidebar.py"
set "cleaner_args=--apply --interactive-sidebar-plan"
goto run
:catalog
set "cleaner_script=clean_codex_catalog.py"
set "cleaner_args=--apply"
goto run
:organize
set "cleaner_script=organize_local_threads.py"
set "cleaner_args=--apply"
goto run
:archives
set "cleaner_script=clean_archived_conversations.py"
set "cleaner_args=--interactive"
goto run
:references
set "cleaner_script=clean_sidebar_references.py"
set "cleaner_args=--interactive"
goto run
:screenshots
set "cleaner_script=plan_sidebar_cleanup.py"
set "cleaner_args=--interactive"
:run
where py.exe >nul 2>nul
if errorlevel 1 goto python
py.exe -3 -X utf8 -u "%~dp0%cleaner_script%" %cleaner_args%
goto finished
:python
python.exe -X utf8 -u "%~dp0%cleaner_script%" %cleaner_args%
:finished
set "cleaner_exit=%errorlevel%"
if "%SIDEBAR_CLEANER_LANG%"=="en" goto result_en
if "%cleaner_exit%"=="0" echo 操作已結束
if "%cleaner_exit%"=="2" echo 尚有項目待處理, 請查看上方提示
if not "%cleaner_exit%"=="0" if not "%cleaner_exit%"=="2" echo 操作已停止, 代碼: %cleaner_exit%
if not "%cleaner_exit%"=="0" echo 日誌: %~dp0logs
echo 按任意鍵關閉
pause >nul
exit /b %cleaner_exit%
:result_en
if "%cleaner_exit%"=="0" echo Operation finished
if "%cleaner_exit%"=="2" echo Items remain pending. See the instructions above.
if not "%cleaner_exit%"=="0" if not "%cleaner_exit%"=="2" echo Operation stopped. Code: %cleaner_exit%
if not "%cleaner_exit%"=="0" echo Logs: %~dp0logs
pause
exit /b %cleaner_exit%
:language
if "%SIDEBAR_CLEANER_LANG%"=="en" (set "SIDEBAR_CLEANER_LANG=zh-TW") else (set "SIDEBAR_CLEANER_LANG=en")
goto menu
