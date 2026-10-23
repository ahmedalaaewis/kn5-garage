@echo off
cd /d "%~dp0"
python -c "import numpy, PIL" 2>nul || python -m pip install -r requirements.txt
python kn5_to_glb.py %*
if not "%~1"=="" pause
