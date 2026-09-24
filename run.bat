@echo off
chcp 65001 >nul
title GoDirect - 下载管理器
cd /d "%~dp0"

echo 正在启动 GoDirect 简易前端下载器...
python main.py

pause
