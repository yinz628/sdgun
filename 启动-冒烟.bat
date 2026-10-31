@echo off
chcp 65001 >nul
title SDGun 水弹社区 - 冒烟测试（守护线程关闭）
rem 开发/验收冒烟专用——守护线程全关(SDGUN_AUTO_SIGN=0 / SDGUN_WATCH=0)，
rem 只保留静态页面 + 代理转发，避免与 VPS 部署形成双端轮询。
rem 日常使用仍用 启动.bat（自动签到 + 关注监控正常开启）。
set SDGUN_AUTO_SIGN=0
set SDGUN_WATCH=0
cd /d %~dp0
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=* delims= " %%b in ("%%a") do set LAN_IP=%%b
)
echo ================================================
echo  SDGun 水弹社区 冒烟测试（守护线程已关闭）
echo  本机访问:   http://127.0.0.1:8000
if defined LAN_IP echo  局域网访问: http://%LAN_IP%:8000
echo  停止: 关闭本窗口 或 Ctrl+C
echo ================================================
python -m uvicorn sdgun_proxy:app --host 0.0.0.0 --port 8000
pause
