import hashlib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from .config import load_config, update_config

logger = logging.getLogger("gofile_api")

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
WT_JS_CACHE = CACHE_DIR / "wt.obf.js"


class GofileAPI:
    """Gofile 官方接口交互与鉴权核心封装"""

    def __init__(self):
        self.config = load_config()
        self.session = requests.Session()
        self._init_session()
        self._cached_salt: Optional[str] = "12af056dacea0b"

    def _init_session(self) -> None:
        """初始化请求会话与代理配置"""
        proxy = self.config.get("proxy")
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self.session.headers.update({
            "User-Agent": self.config.get("user_agent", ""),
            "Referer": "https://gofile.io/",
            "Origin": "https://gofile.io",
        })

    def ensure_account(self) -> str:
        """获取或创建有效的 Gofile 账户 Token"""
        token = self.config.get("account_token", "").strip()
        if token:
            return token

        # 若未配置 Token，则自动向 API 注册访客账户
        url = "https://api.gofile.io/accounts"
        try:
            res = self.session.post(url, timeout=10)
            data = res.json()
            if data.get("status") == "ok":
                token = data["data"]["token"]
                self.config = update_config({"account_token": token})
                logger.info("已自动生成并保存 Gofile 访客 Token: %s", token)
                return token
            raise RuntimeError(f"创建访客账户失败: {data.get('status')}")
        except Exception as e:
            logger.error("请求创建访客账户时发生异常: %s", e)
            raise

    def ensure_wt_script(self, max_age_seconds: int = 14400) -> Path:
        """确保本地缓存有最新的动态签名脚本 wt.obf.js"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        need_fetch = False
        if not WT_JS_CACHE.exists():
            need_fetch = True
        else:
            file_age = time.time() - WT_JS_CACHE.stat().st_mtime
            if file_age > max_age_seconds:
                need_fetch = True

        if need_fetch:
            try:
                res = self.session.get("https://gofile.io/js/wt.obf.js", timeout=10)
                if res.status_code == 200 and len(res.text) > 1000:
                    with open(WT_JS_CACHE, "w", encoding="utf-8") as f:
                        f.write(res.text)
                    logger.info("成功下载并更新本地 wt.obf.js 缓存")
            except Exception as e:
                logger.warning("下载最新 wt.obf.js 失败，尝试使用现有缓存: %s", e)

        if not WT_JS_CACHE.exists():
            raise FileNotFoundError("无法获取 wt.obf.js 脚本文件")
        return WT_JS_CACHE

    def calculate_wt(self, token: str) -> str:
        """计算动态 X-Website-Token 签名"""
        ua = self.config.get("user_agent", "")
        lang = self.config.get("language", "en-US")
        window = str(int(time.time() // 14400))

        # 优先使用纯 Python 配合已知 Salt 计算以获得极致性能
        if self._cached_salt:
            raw = f"{ua}::{lang}::{token}::{window}::{self._cached_salt}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()

        # 备选：通过本地 Node.js 原生运行 wt.obf.js 计算
        js_path = self.ensure_wt_script()
        runner_js = f"""
        Object.defineProperty(navigator, 'userAgent', {{ value: {json.dumps(ua)}, configurable: true }});
        Object.defineProperty(navigator, 'language', {{ value: {json.dumps(lang)}, configurable: true }});
        const fs = require('fs');
        eval(fs.readFileSync({json.dumps(str(js_path))}, 'utf8'));
        console.log(globalThis.generateWT({json.dumps(token)}));
        """
        proc = subprocess.run(
            ["node", "-e", runner_js],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        wt = proc.stdout.strip()
        if wt:
            return wt
        raise RuntimeError(f"计算 X-Website-Token 失败: {proc.stderr}")

    @staticmethod
    def extract_content_id(input_str: str) -> str:
        """从输入的分享链接或纯字符串中提取 contentId"""
        s = input_str.strip()
        if "gofile.io/d/" in s:
            parsed = urlparse(s)
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "d":
                return parts[1]
        return s

    def get_content_info(self, content_id_or_url: str, password: Optional[str] = None) -> Dict[str, Any]:
        """获取指定文件或文件夹的详细信息与直接下载地址"""
        content_id = self.extract_content_id(content_id_or_url)
        token = self.ensure_account()
        wt = self.calculate_wt(token)

        headers = {
            "Authorization": f"Bearer {token}",
            "X-Website-Token": wt,
            "X-BL": self.config.get("language", "en-US"),
        }

        params: Dict[str, Any] = {
            "page": "1",
            "pageSize": "1000",
            "sortField": "name",
            "sortDirection": "1",
        }
        if password:
            pwd_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
            params["password"] = pwd_hash

        url = f"https://api.gofile.io/contents/{content_id}"
        res = self.session.get(url, params=params, headers=headers, timeout=15)
        
        if res.status_code == 429:
            raise RuntimeError("请求过于频繁触发 API 速率限制 (429)，请稍等 1~2 分钟后再试")

        data = res.json()
        status = data.get("status")

        if status == "error-passwordRequired":
            return {"status": "password_required", "content_id": content_id, "message": "该内容受密码保护，请输入提取密码"}
        if status == "error-wrongPassword":
            raise ValueError("提取密码错误，请检查后重试")
        if status != "ok":
            raise RuntimeError(f"获取内容详情失败: {status}")

        return {"status": "ok", "content_id": content_id, "data": data.get("data", {})}

    def collect_all_download_items(self, content_id_or_url: str, password: Optional[str] = None) -> List[Dict[str, Any]]:
        """递归解析目录结构，输出扁平化的可下载文件清单"""
        root_info = self.get_content_info(content_id_or_url, password)
        if root_info.get("status") == "password_required":
            return [{"status": "password_required"}]

        data = root_info.get("data", {})
        items: List[Dict[str, Any]] = []

        def walk(node: Dict[str, Any], current_path: str = ""):
            node_type = node.get("type")
            node_name = node.get("name", "unnamed")
            if node_type == "file":
                items.append({
                    "id": node.get("id"),
                    "name": node_name,
                    "size": node.get("size", 0),
                    "link": node.get("link"),
                    "md5": node.get("md5"),
                    "mimetype": node.get("mimetype"),
                    "relative_path": os.path.join(current_path, node_name),
                })
            elif node_type == "folder":
                children = node.get("children", {})
                folder_path = os.path.join(current_path, node_name) if current_path else ""
                # children 在 API 返回中通常为字典（以 item_id 为 key）或列表
                children_list = children.values() if isinstance(children, dict) else children
                for child in children_list:
                    walk(child, folder_path)

        walk(data, "")
        return items
