`python 3.6
with latest manage position

pyinstaller --onefile --paths C:/Users/king/Videos/TMP/PY3.66/Lib/site-packages app.py


pyinstaller --onefile --console --hidden-import=talib.stream --hidden-import=talib.func ui.py

python setup.py build