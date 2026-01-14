@echo off
REM ========================================
REM TWS Trading GUI - PyInstaller Build Script
REM ========================================

echo.
echo ========================================
echo Building TWS Trading GUI Executable
echo ========================================
echo.

REM Clean previous builds
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "app.spec" del /q "app.spec"

echo Cleaning previous builds... Done
echo.

REM Build executable with PyInstaller
echo Building executable...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "TWS_Trading_GUI" ^
    --icon=NONE ^
    --add-data "Settings.npy;." ^
    --add-data "Cache.npy;." ^
    --hidden-import=ib_insync ^
    --hidden-import=nest_asyncio ^
    --hidden-import=numpy ^
    --hidden-import=talib ^
    --hidden-import=talib.stream ^
    --hidden-import=pandas ^
    --hidden-import=tkinter ^
    --hidden-import=asyncio ^
    --hidden-import=datetime ^
    --hidden-import=logging ^
    --exclude-module=flask ^
    --exclude-module=flask_sqlalchemy ^
    --exclude-module=flask_cors ^
    --exclude-module=werkzeug ^
    --exclude-module=sqlalchemy ^
    --exclude-module=jinja2 ^
    --exclude-module=markupsafe ^
    --exclude-module=itsdangerous ^
    --exclude-module=click ^
    --exclude-module=blinker ^
    --collect-all ib_insync ^
    --collect-all talib ^
    app.py

echo.
if %errorlevel% equ 0 (
    echo ========================================
    echo Build Successful!
    echo ========================================
    echo Executable location: dist\TWS_Trading_GUI.exe
    echo.
    echo You can now run the application by double-clicking:
    echo dist\TWS_Trading_GUI.exe
    echo.
) else (
    echo ========================================
    echo Build Failed!
    echo ========================================
    echo Please check the errors above.
    echo.
)

pause

