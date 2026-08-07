@echo off
REM ============================================================
REM  DELI ES172 Network Diagnostic - double-click to run.
REM
REM  This only reads network information from the device; it
REM  never changes any setting on the device and never sends the
REM  device's API Key or Device UID anywhere.
REM ============================================================

setlocal

set TARGET_IP=192.168.1.8
set /p TARGET_IP="Device IP address (press Enter to use 192.168.1.8): "

echo.
echo Running diagnostic against %TARGET_IP% ...
echo.

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 "%~dp0deli_es172_diagnose.py" %TARGET_IP%
    goto :done
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python "%~dp0deli_es172_diagnose.py" %TARGET_IP%
    goto :done
)

echo.
echo ERROR: Python was not found on this PC.
echo Please install Python 3 from https://www.python.org/downloads/windows/
echo ^(during install, tick "Add python.exe to PATH"^), then double-click this file again.
echo.

:done
echo.
echo ============================================================
echo  Finished. Look above for the two file paths ending in
echo  .json and .txt under a "deli_diagnostic_output" folder
echo  next to this file - please send BOTH of those files back.
echo ============================================================
pause
endlocal
