@echo off
title KN5 Garage
cd /d "%~dp0"
python -c "import numpy, PIL" 2>nul || python -m pip install -r requirements.txt
python app.py %*
pause
