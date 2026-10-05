@echo off
setlocal
chcp 65001 >nul 2>nul
if errorlevel 1 set "SIDEBAR_CLEANER_LANG=en"
title Codex Sidebar Cleaner
where py.exe >nul 2>nul
if errorlevel 1 goto python
py.exe -3 -X utf8 -u "%~dp0start.py" %*
exit /b %errorlevel%
:python
python.exe -X utf8 -u "%~dp0start.py" %*
exit /b %errorlevel%
