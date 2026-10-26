@echo off
setlocal
set "IFC_HELPER=%LOCALAPPDATA%\IngeTrazo-IFC-Import\venv"
echo Creating the IFC helper environment. Python 3.11, 3.12 or 3.13 recommended.
py -3 -m venv "%IFC_HELPER%"
if errorlevel 1 goto failed
"%IFC_HELPER%\Scripts\python.exe" -m pip install -r "%~dp0ifc_import_support\requirements.txt"
if errorlevel 1 goto failed
"%IFC_HELPER%\Scripts\python.exe" -c "import ifcopenshell, ifcopenshell.geom; print('IfcOpenShell', ifcopenshell.version)"
if errorlevel 1 goto failed
echo Helper ready: %IFC_HELPER%\Scripts\python.exe
echo Next run install_windows.bat.
pause
exit /b 0
:failed
echo Setup failed. Install 64-bit Python with the py launcher, then try again.
echo Keep this window open or copy its error message when reporting a problem.
pause
exit /b 1
