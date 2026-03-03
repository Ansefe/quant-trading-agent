@echo off
REM setup_and_run.bat — Crea venv, instala deps, y lanza el servidor de backtesting
REM Uso: Doble click o ejecutar desde terminal

cd /d "%~dp0"

if not exist "venv" (
    echo 🔧 Creando entorno virtual...
    python -m venv venv
)

echo 📦 Instalando dependencias...
call venv\Scripts\activate.bat
pip install -r requirements.txt -q

echo.
echo 🚀 Lanzando servidor de backtesting en http://localhost:8877
echo    Docs: http://localhost:8877/docs
echo.
python api.py
