@echo off
setlocal
cd /d "%~dp0"
title TrialMate chat
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD set "PYCMD=python"
%PYCMD% chat.py %*
if errorlevel 1 pause
