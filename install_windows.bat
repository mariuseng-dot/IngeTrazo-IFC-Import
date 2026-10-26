@echo off
setlocal
set "IFC_PLUGINS=%APPDATA%\ingetrazo\plugins"
if not exist "%IFC_PLUGINS%" mkdir "%IFC_PLUGINS%"
copy /Y "%~dp0ifc_basic_import.py" "%IFC_PLUGINS%\ifc_basic_import.py" >nul
if errorlevel 1 goto failed
if not exist "%IFC_PLUGINS%\ifc_import_support" mkdir "%IFC_PLUGINS%\ifc_import_support"
copy /Y "%~dp0ifc_import_support\*.py" "%IFC_PLUGINS%\ifc_import_support\" >nul
if errorlevel 1 goto failed
copy /Y "%~dp0ifc_import_support\requirements.txt" "%IFC_PLUGINS%\ifc_import_support\requirements.txt" >nul
if errorlevel 1 goto failed
echo Extension installed. Restart IngeTrazo.
echo Use Extensions - Import IFC (basic).
pause
exit /b 0
:failed
echo Installation failed. Check the error above.
pause
exit /b 1
