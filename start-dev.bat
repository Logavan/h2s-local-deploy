e@echo off
title HANA CV to SQL - Dev Server
cd /d %~dp0

:: Kill only dev server processes on ports 3000 and 8080
echo Stopping existing servers...

:: Kill processes on port 3000 (Frontend - Node)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a 2>nul
)

:: Kill processes on port 8080 (Backend - Python)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a 2>nul
)

:: Also kill any Python processes running run.py or flask_app.py
wmic process where "name='python.exe'" call terminate 2>nul
wmic process where "name='pythonw.exe'" call terminate 2>nul

timeout /t 2 /nobreak >nul

echo Starting Backend and Frontend...
echo.

:: Start backend in new window
start "Backend" cmd /k "cd /d %~dp0backend && python run.py"

:: Wait for backend to start
timeout /t 5 /nobreak >nul

:: Start frontend
start "Frontend" cmd /k "cd /d %~dp0frontend && pnpm run dev"

echo.
echo Backend: http://localhost:8080
echo Frontend: http://localhost:3000
echo.
echo Press any key to exit this window (servers will keep running)...
pause >nul
