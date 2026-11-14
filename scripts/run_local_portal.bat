@echo off
title CADCraft AI Portal - Streamlit Web UI
cd /d "%~dp0.."

echo ======================================================================
echo   Menjalankan CADCraft AI Web Portal (Streamlit)
echo ======================================================================
python -m streamlit run app.py
pause
