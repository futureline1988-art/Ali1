@echo off
REM ==============================================================================
REM Attendance Management System - single-command Windows release build
REM ==============================================================================
REM Produces Setup.exe and AttendanceManagementSystem-Portable.exe and
REM assembles them into Release\ alongside the documentation PDFs.
REM
REM Prerequisites (see BUILD_WINDOWS.md for details):
REM   - Windows 10/11, 64-bit
REM   - Python 3.11-3.13 on PATH
REM   - Inno Setup 6 installed, with ISCC.exe on PATH
REM     (default: C:\Program Files (x86)\Inno Setup 6\ISCC.exe)
REM
REM Run from the attendance_system\ project root:
REM   packaging\build_all.bat
REM ==============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0.."

echo ============================================================
echo [1/6] Creating/activating build virtual environment
echo ============================================================
if not exist ".venv-build" (
    python -m venv .venv-build || goto :error
)
call .venv-build\Scripts\activate.bat || goto :error

echo ============================================================
echo [2/6] Installing runtime + build dependencies
echo ============================================================
python -m pip install --upgrade pip || goto :error
pip install -r requirements-runtime.txt || goto :error

echo ============================================================
echo [3/6] Cleaning previous build output
echo ============================================================
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "Release" mkdir "Release"

echo ============================================================
echo [4/6] Building onedir app (for the installer)
echo ============================================================
pyinstaller packaging\pyinstaller\main.spec --noconfirm || goto :error

echo ============================================================
echo [5/6] Building onefile Portable.exe
echo ============================================================
pyinstaller packaging\pyinstaller\main_portable.spec --noconfirm || goto :error
copy /y "dist\AttendanceManagementSystem-Portable.exe" "Release\Portable.exe" || goto :error

echo ============================================================
echo [6/6] Building Setup.exe with Inno Setup
echo ============================================================
where ISCC >nul 2>nul
if %errorlevel%==0 (
    ISCC packaging\installer\setup.iss
) else if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer\setup.iss
) else (
    echo ERROR: ISCC.exe ^(Inno Setup 6^) not found on PATH or in the default
    echo install location. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
    echo then re-run this script, or run ISCC manually:
    echo   ISCC packaging\installer\setup.iss
    goto :error
)
if not exist "Release\Setup.exe" goto :error

echo ============================================================
echo Copying documentation into Release\
echo ============================================================
if exist "docs\README.pdf" copy /y "docs\README.pdf" "Release\README.pdf" >nul
if exist "docs\User Manual.pdf" copy /y "docs\User Manual.pdf" "Release\User Manual.pdf" >nul
if exist "docs\Administrator Manual.pdf" copy /y "docs\Administrator Manual.pdf" "Release\Administrator Manual.pdf" >nul
if exist "CHANGELOG.md" copy /y "CHANGELOG.md" "Release\CHANGELOG.md" >nul

echo.
echo ============================================================
echo BUILD COMPLETE
echo ============================================================
echo Release folder contents:
dir /b "Release"
echo.
echo Done. See Release\ for Setup.exe, Portable.exe, and documentation.
endlocal
exit /b 0

:error
echo.
echo ============================================================
echo BUILD FAILED - see the error above.
echo ============================================================
endlocal
exit /b 1
