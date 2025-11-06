"""
TWS Trading GUI - Requirements Checker
Verifies all dependencies are installed before building
"""

import sys
import subprocess

def check_python_version():
    """Check if Python version is compatible"""
    print("Checking Python version...")
    version = sys.version_info
    if version.major == 3 and version.minor >= 6:
        print(f"✓ Python {version.major}.{version.minor}.{version.micro} - OK")
        return True
    else:
        print(f"✗ Python {version.major}.{version.minor}.{version.micro} - INCOMPATIBLE")
        print("  Required: Python 3.6 or higher")
        return False

def check_package(package_name, import_name=None):
    """Check if a package is installed"""
    if import_name is None:
        import_name = package_name
    
    try:
        __import__(import_name)
        print(f"✓ {package_name} - Installed")
        return True
    except ImportError:
        print(f"✗ {package_name} - NOT INSTALLED")
        return False

def main():
    print("=" * 60)
    print("TWS Trading GUI - Requirements Check")
    print("=" * 60)
    print()
    
    all_ok = True
    
    # Check Python version
    if not check_python_version():
        all_ok = False
    print()
    
    # Check required packages
    print("Checking required packages...")
    packages = [
        ("ib-insync", "ib_insync"),
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("nest-asyncio", "nest_asyncio"),
        ("TA-Lib", "talib"),
    ]
    
    for package_name, import_name in packages:
        if not check_package(package_name, import_name):
            all_ok = False
    
    print()
    
    # Check build tools
    print("Checking build tools...")
    has_pyinstaller = check_package("pyinstaller", "PyInstaller")
    has_cxfreeze = check_package("cx_Freeze", "cx_Freeze")
    
    if not has_pyinstaller and not has_cxfreeze:
        print("\n⚠ WARNING: No build tool found!")
        print("  Install PyInstaller: pip install pyinstaller")
        print("  OR")
        print("  Install cx_Freeze: pip install cx_Freeze")
        all_ok = False
    
    print()
    print("=" * 60)
    
    if all_ok:
        print("✓ All requirements met! You can proceed with building.")
        print()
        print("Next steps:")
        if has_pyinstaller:
            print("  1. Run: build_pyinstaller.bat")
        if has_cxfreeze:
            print("  2. Or run: build_cx_freeze.bat")
    else:
        print("✗ Some requirements are missing!")
        print()
        print("To install all requirements:")
        print("  pip install -r requirements.txt")
        print()
        print("For TA-Lib, use the wheel file:")
        print("  pip install TA_Lib-0.4.19-cp36-cp36m-win_amd64.whl")
    
    print("=" * 60)
    
    return 0 if all_ok else 1

if __name__ == "__main__":
    exit_code = main()
    input("\nPress Enter to exit...")
    sys.exit(exit_code)

