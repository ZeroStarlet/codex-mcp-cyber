@echo off
REM codex-mcp-cyber Setup Script for Windows
REM Delegates to setup.ps1

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
