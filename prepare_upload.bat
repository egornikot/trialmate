@echo off
setlocal
cd /d "%~dp0"
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD set "PYCMD=python"
%PYCMD% prepare_upload.py
pause
