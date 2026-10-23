@echo off
rem Arma los ejecutables de Rescate CLIP en dist\
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --windowed --noupx --name RescateCLIP --icon icono.ico --version-file version_info.txt --add-data "icono.ico;." --onedir --distpath dist\carpeta rescate_clip.py
python -m PyInstaller --noconfirm --clean --windowed --noupx --name RescateCLIP --icon icono.ico --version-file version_info.txt --add-data "icono.ico;." --onefile --distpath dist\unico rescate_clip.py
powershell -NoProfile -Command "Compress-Archive -Path dist\carpeta\RescateCLIP -DestinationPath dist\RescateCLIP.zip -Force"
echo Listo: dist\RescateCLIP.zip y dist\unico\RescateCLIP.exe
pause
