@echo off
REM ========================================
REM TWS Trading GUI - cx_Freeze Build Script
REM ========================================

echo.
echo ========================================
echo Building TWS Trading GUI Executable
echo Using cx_Freeze
echo ========================================
echo.

REM Clean previous builds
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo Cleaning previous builds... Done
echo.

REM Build executable with cx_Freeze
echo Building executable...
python build_cx_freeze.py build

echo.
if %errorlevel% equ 0 (
    echo ========================================
    echo Build Successful!
    echo ========================================
    echo Executable location: build\exe.win-xxx\TWS_Trading_GUI.exe
    echo.
    echo You can now run the application from the build folder.
    echo.
) else (
    echo ========================================
    echo Build Failed!
    echo ========================================
    echo Please check the errors above.
    echo.
)

pause

