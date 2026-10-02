@echo off
rem [WINE ONLY] Run the full test suite under the Windows Python.
cd /d Z:\home\arnaldo\PythonScript22
C:\Python312\python.exe -m unittest discover -p "test_*.py"
