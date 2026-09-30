@echo off
setlocal
chcp 65001 >nul
if "%~1"=="en" set "SIDEBAR_CLEANER_LANG=en"
if not "%SIDEBAR_CLEANER_LANG%"=="en" set "SIDEBAR_CLEANER_LANG=zh-TW"
title Codex Sidebar Cleaner
:menu
set "cleaner_script=clean_codex_catalog.py"
if "%SIDEBAR_CLEANER_LANG%"=="en" goto english
echo 1. 清除已確認刪除的索引 (含專案)
echo 2. 檢查舊專案參照, 重新核對清單並清理
echo 3. 將本機對話整理到獨立區段
echo 4. 清理無法刪除的已封存對話 (本機 / 雲端)
echo 5. 移除選定的側邊欄舊專案參照
echo 6. 分析側邊欄截圖並建立精確封存請求
echo 7. 離開
echo 8. Switch to English
echo 9. 執行已核對的側邊欄修正清單
choice /c 123456789 /n /m "請選擇 [1-9]: "
goto selected
:english
echo 1. Clean confirmed deleted entries (includes projects)
echo 2. Verify, review stale project references, reconcile, then clean
echo 3. Organize local tasks into a separate section
echo 4. Clean archived conversations that cannot be deleted (local / cloud)
echo 5. Remove selected stale sidebar project references
echo 6. Analyze sidebar screenshots and prepare exact cleanup requests
echo 7. Exit
echo 8. 切換為繁體中文
echo 9. Run a reviewed sidebar repair plan
choice /c 123456789 /n /m "Choose [1-9]: "
:selected
if errorlevel 9 goto reviewed
if errorlevel 8 goto language
if errorlevel 7 exit /b 0
if errorlevel 6 goto screenshots
if errorlevel 5 goto references
if errorlevel 4 goto archives
if errorlevel 3 goto organize
if errorlevel 2 goto workflow
set "cleaner_args=--apply"
goto run
:workflow
set "cleaner_script=maintain_sidebar.py"
set "cleaner_args=--apply --review-sidebar-references"
goto run
:reviewed
set "cleaner_script=maintain_sidebar.py"
set "cleaner_args=--apply --interactive-sidebar-plan"
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
if "%SIDEBAR_CLEANER_LANG%"=="en" goto instructions_en
echo 日誌: %~dp0logs
echo 清理本機快取時需要完全退出 Codex; 區段整理不需要
goto launch
:instructions_en
echo Logs: %~dp0logs
echo Cache cleanup needs Codex closed. Section organization does not.
:launch
where py.exe >nul 2>nul
if errorlevel 1 goto python
py.exe -3 -X utf8 -u "%~dp0%cleaner_script%" %cleaner_args%
goto finished
:python
python.exe -X utf8 -u "%~dp0%cleaner_script%" %cleaner_args%
:finished
set "cleaner_exit=%errorlevel%"
if "%SIDEBAR_CLEANER_LANG%"=="en" goto result_en
echo 結束代碼: %cleaner_exit%
if "%cleaner_exit%"=="2" echo 尚有項目待處理或需在網頁確認, 請查看上方提示
echo 按任意鍵關閉
pause >nul
exit /b %cleaner_exit%
:result_en
echo Exit code: %cleaner_exit%
if "%cleaner_exit%"=="2" echo Items remain pending or need web confirmation. See the instructions above.
pause
exit /b %cleaner_exit%
:language
if "%SIDEBAR_CLEANER_LANG%"=="en" (set "SIDEBAR_CLEANER_LANG=zh-TW") else (set "SIDEBAR_CLEANER_LANG=en")
goto menu
