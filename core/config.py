import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"
DEFAULT_DOWNLOAD_DIR = str(Path(__file__).resolve().parent.parent / "downloads")

DEFAULT_CONFIG: Dict[str, Any] = {
    "download_dir": DEFAULT_DOWNLOAD_DIR,
    "account_token": "",
    "max_concurrent_tasks": 3,
    "chunk_threads": 8,
    "proxy": "http://127.0.0.1:27890",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "language": "en-US",
}


def load_config() -> Dict[str, Any]:
    """读取本地配置文件，若不存在则创建默认配置"""
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            merged.update(cfg)
            return merged
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(cfg: Dict[str, Any]) -> None:
    """持久化保存配置到本地文件"""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """更新并保存部分配置项"""
    current = load_config()
    current.update(updates)
    save_config(current)
    return current
