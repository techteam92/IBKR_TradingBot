# TWS Trading GUI - Build Instructions

## 📋 Prerequisites

### 1. Python Installation
- **Python 3.6 or higher** (Python 3.7-3.9 recommended)
- Download from: https://www.python.org/downloads/

### 2. Install Required Packages

#### Option A: Install from requirements.txt (Recommended)
```bash
pip install -r requirements.txt
```

#### Option B: Manual Installation
```bash
# Core packages
pip install ib-insync==0.9.86
pip install nest-asyncio==1.5.1
pip install numpy==1.19.5
pip install pandas==1.1.5

# TA-Lib (use the provided wheel file)
pip install TA_Lib-0.4.19-cp36-cp36m-win_amd64.whl
```

**Note:** TA-Lib requires pre-compiled binaries for Windows. Use the `.whl` file included in the project directory.

### 3. Install Build Tools

#### For PyInstaller (Recommended):
```bash
pip install pyinstaller
```

#### For cx_Freeze (Alternative):
```bash
pip install cx_Freeze
```

---

## 🔨 Building the Executable

### Method 1: PyInstaller (Easiest)

#### Windows:
1. Double-click `build_pyinstaller.bat`
2. Wait for build to complete
3. Find executable in `dist\TWS_Trading_GUI.exe`

#### Command Line:
```bash
pyinstaller --onefile --windowed --name "TWS_Trading_GUI" ^
    --hidden-import=ib_insync ^
    --hidden-import=nest_asyncio ^
    --hidden-import=numpy ^
    --hidden-import=talib ^
    --hidden-import=pandas ^
    --collect-all ib_insync ^
    --collect-all talib ^
    app.py
```

### Method 2: cx_Freeze (Alternative)

#### Windows:
1. Double-click `build_cx_freeze.bat`
2. Wait for build to complete
3. Find executable in `build\exe.win-xxx\TWS_Trading_GUI.exe`

#### Command Line:
```bash
python build_cx_freeze.py build
```

---

## 📦 Distribution

### What to Include:

When distributing the application, include:

1. **The executable file:**
   - `TWS_Trading_GUI.exe`

2. **Configuration files (optional):**
   - `Settings.npy` (if you want to distribute default settings)
   - `IB.log` (will be created automatically)

3. **Documentation:**
   - User manual
   - Trading strategy explanations
   - Risk disclaimers

### What NOT to Include:
- `Cache.npy` (contains user-specific trade data)
- Python source code (unless you want to)
- `__pycache__` folders

---

## 🐛 Troubleshooting

### Problem: "No module named 'talib'"
**Solution:** Install TA-Lib using the wheel file:
```bash
pip install TA_Lib-0.4.19-cp36-cp36m-win_amd64.whl
```

### Problem: "Failed to execute script app"
**Solution:** Try building without `--windowed` flag to see error messages:
```bash
pyinstaller --onefile app.py
```

### Problem: Build succeeds but exe doesn't run
**Solution:** 
1. Check if TWS/IB Gateway is running
2. Verify config settings in `Config.py` before building
3. Run from command line to see error messages:
   ```bash
   dist\TWS_Trading_GUI.exe
   ```

### Problem: Missing DLL errors
**Solution:** 
- Install Visual C++ Redistributable 2015-2019
- Download from: https://support.microsoft.com/en-us/help/2977003/

### Problem: Large file size
**Solution:** Use `--onefile` flag with PyInstaller or compress with UPX:
```bash
pip install pyinstaller[encryption]
pyinstaller --onefile --upx-dir=path/to/upx app.py
```

---

## ⚙️ Configuration Before Building

### Edit `Config.py` for production:

```python
# Connection settings
host = '127.0.0.1'
port = 7497  # 7497 = Paper Trading, 7496 = Live Trading
clientId = 99

# Trading hours
tradingTime = '09:30:00'
tradingEnd = '19:00:00'

# Contract type
ibContract = "Stock"  # or "Forex"
```

---

## 📊 File Size Expectations

- **PyInstaller (--onefile):** ~50-80 MB
- **cx_Freeze:** ~100-150 MB (includes Python runtime)

---

## 🔒 Security Notes

### For Production/Distribution:

1. **Remove debug logging:**
   - Set logging level to WARNING or ERROR in `header.py`

2. **Protect sensitive data:**
   - Don't hardcode passwords
   - Don't include personal trading data

3. **Add error handling:**
   - Graceful connection failures
   - User-friendly error messages

4. **Code signing (optional but recommended):**
   - Sign the .exe file for Windows SmartScreen
   - Prevents "Unknown Publisher" warnings

---

## 📝 Version Information

- **Application Version:** 3.5 (from Config.py)
- **Python Version:** 3.6+
- **Build Date:** Will be embedded in executable

---

## 🎯 Quick Start After Building

1. **Start TWS or IB Gateway** (Paper Trading recommended for testing)
2. **Enable API connections** in TWS:
   - File → Global Configuration → API → Settings
   - Check "Enable ActiveX and Socket Clients"
3. **Run the executable:** `TWS_Trading_GUI.exe`
4. **Test with a small trade** in paper trading account

---

## 📞 Support

If you encounter issues during the build process:

1. Check Python version: `python --version`
2. Verify all packages installed: `pip list`
3. Review build logs for errors
4. Test running the Python script directly first: `python app.py`

---

## ✅ Build Checklist

Before building for production:

- [ ] All dependencies installed
- [ ] Config.py reviewed and configured
- [ ] Tested in paper trading mode
- [ ] Error handling verified
- [ ] Trading hours set correctly
- [ ] Risk management parameters configured
- [ ] Log file location accessible
- [ ] TWS connection tested
- [ ] Build script runs without errors
- [ ] Executable tested on clean Windows machine

---

**Remember:** Always test the executable in paper trading mode before using with real money!

**Risk Disclaimer:** This is automated trading software. Use at your own risk. Always paper trade first.

