@echo off
setlocal
chcp 65001 >nul
set "SIDEBAR_CLEANER_LANG=en"
title Codex Sidebar Cleaner
:menu
cls
echo Codex Sidebar Cleaner
echo.
echo 1. Fix sidebar
echo 2. Clean archives
echo 3. More tools
echo.
echo 0. Exit
choice /c 1230 /n /m "Choose [1-3,0]: "
:selected
if errorlevel 4 exit /b 0
if errorlevel 3 goto advanced
if errorlevel 2 goto archives
set "cleaner_script=maintain_sidebar.py"
set "cleaner_args=--apply --review-sidebar-references"
goto run
:advanced
cls
echo More tools
echo.
echo 1. Apply a reviewed plan
echo 2. Remove old project links
echo 3. Prepare an archive plan
echo 4. Organize local chats
echo 5. Clean deleted chat cache
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
if "%cleaner_exit%"=="0" echo Finished
if "%cleaner_exit%"=="2" echo Action needed. See the message above.
if not "%cleaner_exit%"=="0" if not "%cleaner_exit%"=="2" echo Stopped. Exit code: %cleaner_exit%
if not "%cleaner_exit%"=="0" echo Logs: %~dp0logs
echo Press any key to close
pause >nul
exit /b %cleaner_exit%
