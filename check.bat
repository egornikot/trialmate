@echo off
setlocal
cd /d "%~dp0"
title TrialMate check
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD set "PYCMD=python"
%PYCMD% run.py --check %*
echo.
echo Copy everything above if you need help (no keys are shown).
pause
