import json
import threading
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"
DEFAULT_DOWNLOAD_DIR = str(Path(__file__).resolve().parent.parent / "downloads")
_CONFIG_LOCK = threading.Lock()

DEFAULT_CONFIG: dict[str, Any] = {
    "download_dir": DEFAULT_DOWNLOAD_DIR,
    "account_token": "",
    "max_concurrent_tasks": 3,
    "chunk_threads": 8,
    "proxy": "http://127.0.0.1:27890",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "language": "en-US",
}


def load_config() -> dict[str, Any]:
    """读取本地配置文件，若不存在则创建默认配置"""
    with _CONFIG_LOCK:
        if not CONFIG_FILE.exists():
            _save_config_unlocked(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                merged = DEFAULT_CONFIG.copy()
                merged.update(cfg)
                return merged
        except Exception:
            return DEFAULT_CONFIG.copy()


def _save_config_unlocked(cfg: dict[str, Any]) -> None:
    """内部无锁持久化保存配置到本地文件"""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def save_config(cfg: dict[str, Any]) -> None:
    """线程安全持久化保存配置到本地文件"""
    with _CONFIG_LOCK:
        _save_config_unlocked(cfg)


def update_config(updates: dict[str, Any]) -> dict[str, Any]:
    """线程安全更新并保存部分配置项"""
    with _CONFIG_LOCK:
        current = DEFAULT_CONFIG.copy()
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    current.update(json.load(f))
            except Exception:
                pass
        current.update(updates)
        _save_config_unlocked(current)
        return current

