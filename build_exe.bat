@echo off
rem Builds dist\KN5Garage.exe: one file, no Python needed on the PC that runs it.
rem Needs Python with the requirements and PyInstaller (installed below if missing).
cd /d "%~dp0"
python -c "import PyInstaller" 2>nul || python -m pip install "pyinstaller>=6.6,<7"
python -m pip install -r requirements.txt
python packaging\make_icon.py packaging\kn5garage.ico
rem (paths are absolute: PyInstaller reads relative ones from the --specpath folder)
python -m PyInstaller --noconfirm --clean --onefile --console --name KN5Garage ^
  --icon "%~dp0packaging\kn5garage.ico" ^
  --add-data "%~dp0web;web" ^
  --hidden-import kn5garage.server --hidden-import kn5garage.cli ^
  --exclude-module unittest --exclude-module pydoc --exclude-module test ^
  --distpath dist --workpath build --specpath build ^
  packaging\launcher.py
if errorlevel 1 goto end
echo.
echo Built dist\KN5Garage.exe
:end
if "%~1"=="" pause
