@echo off
rem Zivy prehled prubehu na http://localhost:8800 (jen tento pocitac, jen cteni). Zastaveni: Ctrl+C.
cd /d "%~dp0..\.."
".venv\Scripts\python.exe" -m llm2005.progress_server
