@echo off
chcp 65001 >nul
title Gofile Fetch - 下载管理器
cd /d "%~dp0"

echo 正在启动 Gofile Fetch 简易前端下载器...
python main.py

pause
