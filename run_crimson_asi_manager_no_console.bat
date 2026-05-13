@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 crimson_asi_manager.pyw
) else (
    start "" crimson_asi_manager.pyw
)
exit /b
