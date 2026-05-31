@echo off
REM Double-click to launch the Weekly Reconciliation tool in your browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run reconcile_app.py
pause
