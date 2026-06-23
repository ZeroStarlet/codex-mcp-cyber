@echo off
REM codex-mcp-cyber Uninstall Script for Windows
REM Delegates to uninstall.ps1

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
pause
