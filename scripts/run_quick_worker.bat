@echo off
title CADCRAFT - All-in-One Launcher (Port 7980 + Worker + Web UI)
cd /d "%~dp0.."

echo ======================================================================
echo   CADCRAFT - Launcher (CADCraft 7980 + Antigravity + Worker + Web UI)
echo ======================================================================

echo [1/4] Memeriksa apakah CADCraft Port 7980 sudah aktif...
netstat -ano | findstr ":7980" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo Menjalankan CADCraft ^(Bridged Mode - Port 7980^)...
    start "" "C:\Program Files\CADCraft\cadcraft.exe" --control 7980
) else (
    echo CADCraft sudah berjalan pada port 7980.
)

echo [2/4] Menunggu port 7980 siap ^(LISTENING^)...
:wait_port
netstat -ano | findstr ":7980" | findstr "LISTENING" >nul
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_port
)
echo Port 7980 sudah aktif!

echo [3/4] Menjalankan Streamlit Web UI ^(Port 8501^)...
netstat -ano | findstr ":8501" | findstr "LISTENING" >nul
if errorlevel 1 (
    start "CADCRAFT Web UI" cmd /k "python -m streamlit run app.py"
) else (
    echo Streamlit Web UI sudah berjalan pada port 8501.
)

echo [4/4] Menjalankan Local Worker ^(Terhubung ke Antigravity CLI ^& MCP CADCraft^)...
python worker\local_worker.py
pause
