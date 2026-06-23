@echo off
:: SCCG Uninstall Launcher for Windows
:: Double-click this file to run uninstall.ps1

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
pause
