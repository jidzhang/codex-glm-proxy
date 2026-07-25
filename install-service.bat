@echo off
setlocal

rem ============================================================
rem  Install codex-glm-proxy as a Windows service via NSSM.
rem  Run as Administrator (a shell that has GLM_API_KEY set).
rem ============================================================

cd /d "%~dp0"

set SVC=codex-glm-proxy
set NSSM=nssm.exe
set PARAM=%cd%\proxy.py

rem --- required tools must be present ---
where %NSSM% >nul 2>&1
if errorlevel 1 (
    echo [ERROR] %NSSM% not found in PATH. Install NSSM and add it to PATH.
    exit /b 1
)

rem --- resolve full path to python.exe (LocalSystem PATH may differ) ---
set PY=
for /f "delims=" %%i in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%i"
)
if not defined PY (
    echo [ERROR] python.exe not found in PATH. Add Python to PATH and retry.
    exit /b 1
)

rem --- required secret must be present in this (admin) shell ---
if not defined GLM_API_KEY (
    echo [ERROR] GLM_API_KEY is not set in this environment.
    echo         Run:  setx GLM_API_KEY "your-key"
    echo         then open a NEW Admin shell so it is inherited, and retry.
    exit /b 1
)

echo ============================================
echo   setup %SVC% using NSSM
echo ============================================
echo.

echo [1/7] Removing any previous instance ...
"%NSSM%" stop   %SVC% >nul 2>&1
"%NSSM%" remove %SVC% confirm >nul 2>&1
rem Give SCM a moment to finish deletion before recreating the service.
timeout /t 2 /nobreak >nul 2>&1
echo       done.
echo.

echo [2/7] Installing service ^("%PY%"^) ...
"%NSSM%" install %SVC% "%PY%" "%PARAM%"
if errorlevel 1 (
    echo [ERROR] Install failed. Are you running as Administrator?
    exit /b 1
)
echo.

echo [3/7] Configuring environment (GLM_API_KEY ...) ...
"%NSSM%" set %SVC% AppDirectory "%cd%"
if errorlevel 1 goto :configure_failed

set "ENV_EXTRA="GLM_API_KEY=%GLM_API_KEY%""
if defined GLM_BASE_URL   set "ENV_EXTRA=%ENV_EXTRA% "GLM_BASE_URL=%GLM_BASE_URL%""
if defined PROXY_PORT     set "ENV_EXTRA=%ENV_EXTRA% "PROXY_PORT=%PROXY_PORT%""
if defined GLM_HTTP_PROXY  set "ENV_EXTRA=%ENV_EXTRA% "GLM_HTTP_PROXY=%GLM_HTTP_PROXY%""
if defined GLM_HTTPS_PROXY set "ENV_EXTRA=%ENV_EXTRA% "GLM_HTTPS_PROXY=%GLM_HTTPS_PROXY%""
if defined NO_PROXY        set "ENV_EXTRA=%ENV_EXTRA% "NO_PROXY=%NO_PROXY%""
"%NSSM%" set %SVC% AppEnvironmentExtra %ENV_EXTRA%
if errorlevel 1 goto :configure_failed
echo.

echo [4/7] Configuring logging ...
"%NSSM%" set %SVC% AppStdout "%cd%\nssm.out.log"
if errorlevel 1 goto :configure_failed
"%NSSM%" set %SVC% AppStderr "%cd%\nssm.err.log"
if errorlevel 1 goto :configure_failed
rem CreationDisposition = 2 (CREATE_ALWAYS): truncate stdout/stderr on each service
rem start, so the logs only hold the current run. Rotation is left off (default)
rem on purpose -- with little output there is no need to keep timestamped history.
"%NSSM%" set %SVC% AppStdoutCreationDisposition 2
if errorlevel 1 goto :configure_failed
"%NSSM%" set %SVC% AppStderrCreationDisposition 2
if errorlevel 1 goto :configure_failed
echo.

echo [5/7] Configuring restart-on-failure ...
"%NSSM%" set %SVC% AppExit Default Restart
if errorlevel 1 goto :configure_failed
"%NSSM%" set %SVC% AppRestartDelay 5000
if errorlevel 1 goto :configure_failed
echo.

echo [6/7] Starting service ...
"%NSSM%" start %SVC%
if errorlevel 1 (
    echo [ERROR] Start failed. Status + log follow ...
    "%NSSM%" status %SVC% 2>nul
    if exist "%cd%\nssm.err.log" (
        echo ----- nssm.err.log -----
        type "%cd%\nssm.err.log"
    )
    exit /b 1
)
echo.

echo [7/7] Final status ...
"%NSSM%" status %SVC%
echo.
echo Logs: %cd%\nssm.err.log
echo ============================================
echo   Install complete.
echo ============================================
exit /b 0

:configure_failed
echo [ERROR] Failed to configure service. Check that the service exists and you are Administrator.
exit /b 1
