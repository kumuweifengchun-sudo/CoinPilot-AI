@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\release\build_installer.ps1" %*
set "releaseExitCode=%ERRORLEVEL%"
if not "%releaseExitCode%"=="0" echo Release build failed. See the error above.
if "%~1"=="" pause
exit /b %releaseExitCode%
