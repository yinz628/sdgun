@echo off
chcp 65001 >nul
title SDGun 水弹社区 - 本地服务
cd /d %~dp0
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=* delims= " %%b in ("%%a") do set LAN_IP=%%b
)
echo ================================================
echo  SDGun 水弹社区 网页版（局域网可访问）
echo  本机访问:   http://127.0.0.1:8000
if defined LAN_IP echo  局域网访问: http://%LAN_IP%:8000
echo  停止: 关闭本窗口 或 Ctrl+C
echo ================================================
python -m uvicorn sdgun_proxy:app --host 0.0.0.0 --port 8000
pause
