"""
TWS Trading GUI - Setup Script (cx_Freeze)
Legacy setup.py - Use build_cx_freeze.py for updated version

For building:
    python setup.py build

Or use the new build scripts:
    - build_pyinstaller.bat (recommended)
    - build_cx_freeze.bat
"""

import sys
import os
from cx_Freeze import setup, Executable
from datetime import datetime

# Determine base
base = None
if sys.platform == "win32":
    base = "Win32GUI"  # Hides console window

build_exe_options = {
	"packages": [
		"asyncio",
		"encodings",
		"pandas",
		"tkinter",
		"talib",
		"talib.stream",
		"numpy",
		"ib_insync",
		"nest_asyncio",
		"datetime",
		"logging",
		"traceback"
	],
	"excludes": [
		"matplotlib",
		"scipy",
		"PyQt5",
		"jinja2"
	],
	"include_files": [
		# Include DLLs if present
		# os.path.join(os.path.dirname(os.path.abspath(__file__)), 'DLLs', 'sqlite3.dll'),
		# os.path.join(os.path.dirname(os.path.abspath(__file__)), 'DLLs', 'vcredist_x64.exe')
	],
	"include_msvcr": True,
	"optimize": 2
}

setup(
	name="TWS Trading GUI",
	version="3.5",
	description="Trading GUI for Interactive Brokers TWS - Automated Trading Strategies",
	options={"build_exe": build_exe_options},
	executables=[Executable("app.py", base=base, target_name="TWS_Trading_GUI.exe")]
)