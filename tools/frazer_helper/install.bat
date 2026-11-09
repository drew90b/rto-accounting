@echo off
rem One-time setup on the Frazer computer. Run by double-clicking. Needs Python 3.9 or newer (python.org, "Add python.exe to PATH").
setlocal
set "DIR=C:\FrazerHelper"
if not exist "%DIR%" mkdir "%DIR%"
if not exist "%DIR%\runs" mkdir "%DIR%\runs"
copy /Y "%~dp0frazer_helper.py" "%DIR%\frazer_helper.py" >nul
if not exist "%DIR%\config.json" copy "%~dp0config.example.json" "%DIR%\config.json" >nul
rem Desktop shortcut file that opens the one-button window
> "%DIR%\Send Frazer exports.bat" echo @echo off
>> "%DIR%\Send Frazer exports.bat" echo cd /d "%DIR%"
>> "%DIR%\Send Frazer exports.bat" echo pythonw frazer_helper.py window --config "%DIR%\config.json"
copy /Y "%DIR%\Send Frazer exports.bat" "%USERPROFILE%\Desktop\Send Frazer exports.bat" >nul
python --version || (echo Python was not found. Install it from python.org first. & pause & exit /b 1)
echo.
echo Installed in %DIR%.
echo Next: open %DIR%\config.json in Notepad and fill in the token and the folder where Frazer saves exports.
echo Then double-click "Send Frazer exports" on the desktop. To test the settings first:
echo     python %DIR%\frazer_helper.py check --config %DIR%\config.json
pause
