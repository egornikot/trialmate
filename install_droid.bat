@echo off
setlocal
cd /d "%~dp0"
title Install Factory Droid for TrialMate
rem Find Python by running it (more reliable than "where" with the Python 3.14 install manager).
set "PYCMD="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if not defined PYCMD (
  python --version >nul 2>nul
  if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
  echo Could not start Python from this window. Open PowerShell and run:
  echo   py -m pip install "droid-sdk[mcp]" tzdata certifi
  pause
  exit /b 1
)
echo Installing the Droid Python SDK and helpers with %PYCMD% ...
%PYCMD% -m pip install --upgrade "droid-sdk[mcp]" tzdata certifi
if errorlevel 1 (
  echo.
  echo pip reported an error above. Send a screenshot of it to Claude.
  pause
  exit /b 1
)
echo.
if exist "%USERPROFILE%\bin\droid.exe" (
  echo Factory droid CLI is already installed.
) else (
  echo Installing the Factory droid CLI, official installer from app.factory.ai ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://app.factory.ai/cli/windows | iex"
)
echo.
echo Done. Close TrialMate if it is running, then double-click reset.bat.
pause
