"""
TWS Trading GUI - cx_Freeze Build Script
Builds standalone executable for Windows
"""

import sys
import os
from cx_Freeze import setup, Executable

# Determine base
base = None
if sys.platform == "win32":
    base = "Win32GUI"  # Use this to hide console window

# Build options
build_exe_options = {
    "packages": [
        "asyncio",
        "encodings",
        "tkinter",
        "talib",
        "talib.stream",
        "pandas",
        "numpy",
        "ib_insync",
        "nest_asyncio",
        "logging",
        "datetime",
        "traceback",
        "threading",
        "math",
        "random",
        "os",
        "sys"
    ],
    "excludes": [
        "matplotlib",
        "scipy",
        "PyQt5",
        "PyQt4"
    ],
    "include_files": [
        # Include DLLs if present
        # ("DLLs/sqlite3.dll", "sqlite3.dll"),
        # Include .npy files if they exist
        # ("Settings.npy", "Settings.npy"),
        # ("Cache.npy", "Cache.npy"),
    ],
    "include_msvcr": True,
    "optimize": 2
}

# Executable options
executables = [
    Executable(
        script="app.py",
        base=base,
        target_name="TWS_Trading_GUI.exe",
        icon=None  # Add icon path if you have one: "icon.ico"
    )
]

# Setup
setup(
    name="TWS Trading GUI",
    version="3.5",
    description="Trading GUI for Interactive Brokers TWS - Automated Trading Strategies",
    author="Your Name",
    options={"build_exe": build_exe_options},
    executables=executables
)

