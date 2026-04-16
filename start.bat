@echo off
netstat -ano | findstr ":8787" | findstr /v "^$" > nul
if %errorlevel% == 0 (
    echo Proxy already running.
) else (
    echo Starting JIRA proxy...
    start "JIRA Proxy" /MIN python "%~dp0jira_proxy.py"
    timeout /t 2 /nobreak > nul
)
start "" "%~dp0qa_code_generator.html"
