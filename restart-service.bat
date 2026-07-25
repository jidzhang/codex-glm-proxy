@echo off
setlocal

rem ============================================================
rem  Restart the codex-glm-proxy Windows service (NSSM).
rem  Run as Administrator.
rem ============================================================

cd /d "%~dp0"

set SVC=codex-glm-proxy
set NSSM=nssm.exe

where %NSSM% >nul 2>&1
if errorlevel 1 (
    echo [ERROR] %NSSM% not found in PATH. Install NSSM and add it to PATH.
    exit /b 1
)

echo ============================================
echo   restart %SVC% (NSSM)
echo ============================================
echo.

echo [1/3] Current service info ---
echo       status:
"%NSSM%" status %SVC% 2>nul
echo       application:
"%NSSM%" get %SVC% Application 2>nul
echo       parameters:
"%NSSM%" get %SVC% AppParameters 2>nul
echo.

echo [2/3] Stopping service ...
"%NSSM%" stop %SVC% >nul 2>&1
echo       done.
echo.

echo [3/3] Starting service ...
"%NSSM%" start %SVC%
if errorlevel 1 (
    echo [ERROR] Failed to start service. Check nssm.err.log for details.
    exit /b 1
)
echo       done.
echo.

echo ============================================
echo   Restart complete.
echo ============================================
exit /b 0
