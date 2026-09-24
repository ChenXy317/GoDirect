@echo off
chcp 65001 >nul
title GoDirect - 高速下载管理器
cd /d "%~dp0"

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    where py >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo [错误] 未在系统 PATH 中检测到 Python 环境。
        echo 请先前往 https://www.python.org/ 下载并安装 Python 3.10+，
        echo 并务必勾选 "Add python.exe to PATH"。
        echo.
        pause
        exit /b 1
    ) else (
        set "PY_CMD=py -3"
    )
) else (
    set "PY_CMD=python"
)

%PY_CMD% -c "import fastapi, uvicorn, requests, pydantic" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [提示] 正在自动安装运行依赖项...
    %PY_CMD% -m pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo [错误] 依赖项安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

%PY_CMD% main.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [异常] 程序已异常退出，退出码: %ERRORLEVEL%
    pause
)