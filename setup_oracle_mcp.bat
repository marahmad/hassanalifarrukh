@echo off
setlocal
title Setup Oracle Cloud MCP Server - Antigravity IDE
cd /d "%~dp0"

echo ========================================================
echo   ORACLE CLOUD INFRASTRUCTURE (OCI) MCP SETUP
echo ========================================================
echo.

if not exist "C:\tmp" (
    mkdir "C:\tmp"
)

where uv >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [info] Installing uv via winget...
    winget install --id=astral-sh.uv -e --accept-package-agreements --accept-source-agreements
    set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
)

where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    uv run --python 3.13 "%~dp0.agents\skills\oracle-oci-mcp\scripts\setup_oracle_mcp.py" %*
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        python "%~dp0.agents\skills\oracle-oci-mcp\scripts\setup_oracle_mcp.py" %*
    ) else (
        echo [err] Neither uv nor python was found on PATH.
        echo       Please restart your terminal after installing uv and re-run this script.
        pause
        exit /b 1
    )
)

echo.
echo ========================================================
echo   NEXT IN ANTIGRAVITY IDE:
echo   ... ^> MCP Servers ^> Manage MCP Servers ^> Refresh
echo ========================================================
pause
