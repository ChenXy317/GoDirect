<div align="center">

# GoDirect

**面向 Gofile 的现代化全功能独立下载管理器与动态鉴权工具**  
*A modern, high-speed independent download manager and dynamic authentication solution for Gofile.*

[![Python](https://img.shields.io/badge/Python-3.10%2B-black?style=flat-square&logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-black?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-7%20Passed-black?style=flat-square)](https://github.com/)
[![License](https://img.shields.io/badge/License-MIT-black?style=flat-square)](LICENSE)

[**中文说明**](#-中文说明) &nbsp;|&nbsp; [**English Documentation**](#-english-documentation)

</div>

---

## 🇨🇳 中文说明

### 📖 项目简介

**GoDirect** 是一款专为解决 Gofile 网盘生态常见限制（防盗链 302 重定向、动态混淆签名过期、外部下载工具抓取失败等）而设计的全功能独立下载工具。

项目集成了**动态算法签名模拟**、**独立 HTTP Range 多线程分块续传引擎**、**黑白现代极简主义控制台**以及**外部工具无缝联动（IDM 与 cURL）**，无需依赖复杂外部环境，即可实现大文件与多级目录树的高速稳定拉取。

---

### ✨ 核心特性

1. **智能破解防盗链与会话鉴权**
   - 自动在下载全链路封装 `Cookie: accountToken=...`、`Referer: https://gofile.io/` 与标准浏览器 `User-Agent`，彻底根除直链被服务端 302 重定向回网页或仅下载到几 KB HTML 网页的现象。
2. **2026 动态加签模拟（`X-Website-Token`）**
   - 深入分析 Gofile 混淆加签脚本（`/js/wt.obf.js`），支持动态提取 Salt 并以毫秒级 SHA-256 算法生成合规签名，同时内置 Node.js 动态计算回退引擎，告别 `401 Unauthorized` 与 `error-notPremium` 限制。
3. **独立多线程分块下载引擎**
   - 基于 HTTP Range 协议实现大文件多线程并发分块下载与断点续传（基于 `.part` 与 `.part.meta` 文件）。
   - 自动规避 Windows 系统保留设备名（`CON`, `PRN`, `AUX`, `NUL` 等）并防御恶意路径穿越。
   - 支持多任务并发调度、随时暂停、继续、取消及状态自愈。
4. **全双工 WebSocket 实时看板**
   - 后台以 500ms 频率毫秒级推送瞬时传输速度、剩余时间（ETA）、动态进度条与系统事件，界面零轮询开销。
5. **IDM 与 cURL 一键联动**
   - 支持通过 Windows COM 接口（`ICIDMLinkTransmitter2`）将任务及凭据批量静默投递至本地 IDM 队列。
   - 支持一键导出携带鉴权 Cookie 的 Windows 批处理 cURL 脚本（`.bat`）。
6. **黑白现代极简主义 Web 控制台**
   - 参考 Linear / Vercel 设计风格，纯粹高反差黑白配色，等宽数字排版，无繁琐 npm 构建依赖，轻盈迅速。

---

### 🚀 快速启动

#### 方式一：Windows 双击一键启动（推荐）
直接双击根目录下的 **`run.bat`**：
* 脚本会自动检查系统 Python 环境与运行依赖；
* 后台服务就绪后，将自动调用系统默认浏览器打开管理器界面（默认地址：`http://127.0.0.1:8000`）。

#### 方式二：命令行手动启动
```bash
# 1. 安装核心依赖
pip install -r requirements.txt

# 2. 运行主程序
python main.py
```

---

### ⚙️ 配置说明

您可以在网页右上角的“偏好设置”弹窗中可视化修改，配置将实时持久化至根目录下的 `config.json`：

| 配置项 | 键名 | 默认值 | 详细说明 |
| :--- | :--- | :--- | :--- |
| **下载保存目录** | `download_dir` | `./downloads` | 文件下载完成后的本地绝对路径。 |
| **网络代理地址** | `proxy` | `http://127.0.0.1:27890` | 支持 HTTP/HTTPS 代理，用于规避国内网络连接 Gofile 时的阻断。留空表示直连。 |
| **最大并发任务数** | `max_concurrent_tasks` | `3` | 同时执行下载的任务数量上限（1~10）。 |
| **单文件分块线程数** | `chunk_threads` | `4` | 大文件并发拉取时的 HTTP Range 分块线程数（1~16）。 |
| **自定义账户 Token** | `account_token` | `""` | 普通访客留空即可，程序会自动生成并持久化；填入个人 VIP Token 可解除速率限制。 |

---

### 🛠️ 技术架构

```
GoDirect/
├── core/
│   ├── config.py         # 线程安全的应用配置管理
│   ├── downloader.py     # HTTP Range 多线程分块断点续传下载引擎
│   ├── gofile_api.py     # Gofile 协议解析、动态 Salt 提取与鉴权计算
│   └── idm_interop.py    # 本地 IDM COM 反射联动与 cURL 批处理生成
├── static/
│   ├── app.js            # 现代化前端业务逻辑与 WebSocket 客户端
│   ├── index.html        # 单页控制台 HTML 布局
│   └── style.css         # 黑白现代极简主义设计系统
├── tests/
│   └── test_core.py      # 核心逻辑自动化单元测试套件
├── main.py               # 端口自适应寻址与浏览器唤起入口
├── server.py             # FastAPI 路由与 WebSocket 事件流
├── requirements.txt      # 核心运行依赖清单
└── run.bat               # Windows 一键自检启动脚本
```

---

<br/>

## 🇬🇧 English Documentation

### 📖 Overview

**GoDirect** is a modern, standalone download manager and bypass tool designed specifically for the Gofile ecosystem. It addresses common limitations such as anti-hotlinking 302 redirects, dynamic obfuscated signature failures, and third-party downloader incompatibilities.

Powered by a **dynamic authentication engine**, an **independent multi-threaded HTTP Range chunk downloader**, a **monochrome minimalist web dashboard**, and **seamless IDM / cURL export**, GoDirect provides a fast, stable, and completely self-contained downloading experience without external tool dependencies.

---

### ✨ Key Features

1. **Anti-Hotlinking & Session Bypass**
   - Automatically injects `Cookie: accountToken=...`, `Referer: https://gofile.io/`, and matched `User-Agent` headers across all download requests, preventing 302 redirects to web pages or empty HTML file corruptions.
2. **2026 Dynamic Signature Simulation (`X-Website-Token`)**
   - Reverse-engineers Gofile's obfuscated security script (`/js/wt.obf.js`), extracts dynamic salt values, and generates SHA-256 tokens in milliseconds. Includes a Node.js fallback runner to prevent `401 Unauthorized` errors.
3. **Independent Multi-Threaded Chunk Downloader**
   - Concurrent range downloading with automated chunk splitting for large files.
   - Resumable downloads backed by `.part` and `.part.meta` metadata preservation.
   - Built-in filename sanitization protecting against Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, etc.) and path traversal attacks.
   - Task controls: pause, resume, cancel, and automatic error recovery.
4. **Full-Duplex Real-Time Telemetry**
   - Broadcasts real-time metrics (speed, progress, ETA, and state changes) every 500ms via WebSocket, eliminating polling overhead.
5. **IDM Integration & cURL Batch Export**
   - Silently pushes tasks with complete authentication tokens to local Internet Download Manager (IDM) instances via C# COM interop (`ICIDMLinkTransmitter2`).
   - One-click export of self-contained, escaped Windows batch scripts (`.bat`) for native `curl.exe` downloads.
6. **Monochrome Minimalist Web UI**
   - Inspired by Linear and Vercel design aesthetics. High-contrast black-and-white theme, monospace tabular figures, zero npm dependencies, ultra-responsive and lightweight.

---

### 🚀 Quick Start

#### Option 1: Windows One-Click Launch (Recommended)
Double-click **`run.bat`** in the project root directory:
* Automatically verifies Python availability and installs missing dependencies if needed;
* Opens the web console in your default browser once the service is ready (Default URL: `http://127.0.0.1:8000`).

#### Option 2: Command Line Launch
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the service
python main.py
```

---

### ⚙️ Configuration

Settings can be managed either via the web dashboard (top-right "Settings" button) or directly in `config.json`:

| Parameter | Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Download Folder** | `download_dir` | `./downloads` | Absolute local path for saving downloaded files. |
| **Proxy Address** | `proxy` | `http://127.0.0.1:27890` | HTTP/HTTPS proxy to circumvent network throttling or connection resets. Leave empty for direct connection. |
| **Max Concurrent Tasks** | `max_concurrent_tasks` | `3` | Maximum number of files downloaded concurrently (1–10). |
| **Chunk Threads** | `chunk_threads` | `4` | Number of concurrent HTTP Range threads per large file (1–16). |
| **Account Token** | `account_token` | `""` | Optional. Guest tokens are automatically generated and saved. Enter your VIP/Premium token to remove bandwidth limits. |

---

### 🧪 Automated Testing

Verify the codebase integrity by running pytest:
```bash
pytest
```
Covers filename security filters, metrics calculation, task lifecycle control, API endpoints, cURL script generation, and chunk resumption metadata persistence.

---

### 📄 License

This project is licensed under the [MIT License](LICENSE).
