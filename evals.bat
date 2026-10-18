@echo off
setlocal
cd /d "%~dp0"
title TrialMate evals
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD set "PYCMD=python"
%PYCMD% evals\run_evals.py %*
echo.
echo Results saved in evals\results\latest.md
pause
