@echo off
REM Build OceanShare para Windows
REM Requer Python 3.9+ instalado e no PATH

echo Instalando dependencias...
python -m pip install --upgrade pip
python -m pip install pywebview pyinstaller

echo Compilando OceanShare.exe...
pyinstaller --clean --noconfirm surfshare.spec

echo.
echo Pronto! O executavel esta em: dist\OceanShare.exe
pause
