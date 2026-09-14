@echo off
setlocal
title Codex Sidebar Cleaner
set "cleaner_script=clean_codex_catalog.py"
echo 1. Clean confirmed deleted entries (includes projects)
echo 2. Verify, scan, reconcile with cloud, then clean
echo 3. Organize local tasks into a separate section
echo 4. Exit
choice /c 1234 /n /m "Choose [1-4]: "
if errorlevel 4 exit /b 0
if errorlevel 3 goto organize
if errorlevel 2 goto workflow
set "cleaner_args=--apply"
goto run
:workflow
set "cleaner_script=maintain_sidebar.py"
set "cleaner_args=--apply"
goto run
:organize
set "cleaner_script=organize_local_threads.py"
set "cleaner_args=--apply"
:run
echo Logs: %~dp0logs
echo Cache cleanup needs Codex closed. Section organization does not.
where py.exe >nul 2>nul
if errorlevel 1 goto python
py.exe -3 -X utf8 -u "%~dp0%cleaner_script%" %cleaner_args%
goto finished
:python
python.exe -X utf8 -u "%~dp0%cleaner_script%" %cleaner_args%
:finished
set "cleaner_exit=%errorlevel%"
echo Exit code: %cleaner_exit%
pause
exit /b %cleaner_exit%
