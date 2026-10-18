@echo off
setlocal
cd /d "%~dp0"
title TrialMate
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env from .env.example. Open it in Notepad to add your API keys.
)
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
  where python >nul 2>nul && set "PYCMD=python"
)
if not defined PYCMD (
  echo.
  echo Python isn't installed. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer. Then run this again.
  echo.
  pause
  exit /b 1
)
%PYCMD% run.py %*
if errorlevel 1 pause
