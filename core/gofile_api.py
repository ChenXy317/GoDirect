import hashlib
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any
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
        self._cached_salt: str | None = "12af056dacea0b"

    def _init_session(self) -> None:
        """初始化请求会话与代理配置"""
        self.config = load_config()
        proxy = self.config.get("proxy", "").strip()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        else:
            self.session.proxies = {}

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

    def extract_salt_from_script(self, script_text: str) -> str | None:
        """从 wt.obf.js 脚本中提取动态 Salt 值"""
        try:
            hex_strings = re.findall(r"'((?:\\x[0-9a-fA-F]{2})+)'", script_text)
            for raw in hex_strings:
                decoded = bytes.fromhex(raw.replace("\\x", "")).decode("utf-8", errors="ignore")
                if len(decoded) == 14 and all(c in "0123456789abcdef" for c in decoded):
                    return decoded
        except Exception as e:
            logger.warning("解析脚本 Salt 发生异常: %s", e)
        return None

    def ensure_wt_script(self, force: bool = False, max_age_seconds: int = 14400) -> Path:
        """确保本地缓存有最新的动态签名脚本 wt.obf.js"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        need_fetch = force or (not WT_JS_CACHE.exists())
        if not need_fetch:
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
                    new_salt = self.extract_salt_from_script(res.text)
                    if new_salt:
                        self._cached_salt = new_salt
            except Exception as e:
                logger.warning("下载最新 wt.obf.js 失败，尝试使用现有缓存: %s", e)

        if not WT_JS_CACHE.exists():
            raise FileNotFoundError("无法获取 wt.obf.js 脚本文件")

        if not self._cached_salt:
            try:
                with open(WT_JS_CACHE, "r", encoding="utf-8") as f:
                    self._cached_salt = self.extract_salt_from_script(f.read())
            except Exception:
                pass

        return WT_JS_CACHE

    def calculate_wt(self, token: str) -> str:
        """计算动态 X-Website-Token 签名"""
        ua = self.config.get("user_agent", "")
        lang = self.config.get("language", "en-US")
        window = str(int(time.time() // 14400))

        if not self._cached_salt:
            try:
                self.ensure_wt_script()
            except Exception as e:
                logger.warning("初始化签名脚本失败: %s", e)

        if self._cached_salt:
            raw = f"{ua}::{lang}::{token}::{window}::{self._cached_salt}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()

        js_path = self.ensure_wt_script()
        runner_js = f"""
        if (typeof navigator === 'undefined') {{ globalThis.navigator = {{}}; }}
        Object.defineProperty(navigator, 'userAgent', {{ value: {json.dumps(ua)}, configurable: true }});
        Object.defineProperty(navigator, 'language', {{ value: {json.dumps(lang)}, configurable: true }});
        const fs = require('fs');
        eval(fs.readFileSync({json.dumps(str(js_path))}, 'utf8'));
        console.log(globalThis.generateWT({json.dumps(token)}));
        """
        try:
            proc = subprocess.run(
                ["node", "-e", runner_js],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            wt = proc.stdout.strip()
            if wt:
                return wt
        except Exception as e:
            logger.error("Node.js 运行签名脚本失败: %s", e)

        fallback_raw = f"{ua}::{lang}::{token}::{window}::12af056dacea0b"
        return hashlib.sha256(fallback_raw.encode("utf-8")).hexdigest()

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

    def get_content_info(
        self, content_id_or_url: str, password: str | None = None, retry_count: int = 0
    ) -> dict[str, Any]:
        """获取指定文件或文件夹的详细信息与直接下载地址"""
        content_id = self.extract_content_id(content_id_or_url)
        token = self.ensure_account()
        wt = self.calculate_wt(token)

        headers = {
            "Authorization": f"Bearer {token}",
            "X-Website-Token": wt,
            "X-BL": self.config.get("language", "en-US"),
        }

        params: dict[str, Any] = {
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

        if res.status_code in (401, 403) and retry_count == 0:
            logger.info("检测到凭据或签名失效 (HTTP %s)，正在重刷新签名脚本重试...", res.status_code)
            self.ensure_wt_script(force=True)
            return self.get_content_info(content_id_or_url, password, retry_count=1)

        try:
            data = res.json()
        except Exception:
            raise RuntimeError(f"Gofile 接口响应异常 (HTTP {res.status_code})，可能受到网络阻断或服务端维护")

        status = data.get("status")
        if status == "error-passwordRequired":
            return {"status": "password_required", "content_id": content_id, "message": "该内容受密码保护，请输入提取密码"}
        if status == "error-wrongPassword":
            raise ValueError("提取密码错误，请检查后重试")
        if status != "ok":
            raise RuntimeError(f"获取内容详情失败: {status}")

        return {"status": "ok", "content_id": content_id, "data": data.get("data", {})}

    def collect_all_download_items(
        self, content_id_or_url: str, password: str | None = None
    ) -> list[dict[str, Any]]:
        """递归解析目录结构，输出扁平化的可下载文件清单"""
        root_info = self.get_content_info(content_id_or_url, password)
        if root_info.get("status") == "password_required":
            return [{"status": "password_required"}]

        data = root_info.get("data", {})
        items: list[dict[str, Any]] = []

        def walk(node: dict[str, Any], current_path: str = ""):
            node_type = node.get("type")
            node_name = node.get("name", "unnamed")
            safe_rel_dir = current_path.replace("\\", "/").strip("/")

            if node_type == "file":
                rel_path = f"{safe_rel_dir}/{node_name}" if safe_rel_dir else node_name
                items.append({
                    "id": node.get("id"),
                    "name": node_name,
                    "size": node.get("size", 0),
                    "link": node.get("link"),
                    "md5": node.get("md5"),
                    "mimetype": node.get("mimetype"),
                    "relative_path": rel_path,
                })
            elif node_type == "folder":
                folder_path = f"{safe_rel_dir}/{node_name}" if safe_rel_dir else node_name
                children = node.get("children")

                if children is None and node.get("id") and node.get("id") != data.get("id"):
                    try:
                        sub_info = self.get_content_info(node["id"], password)
                        sub_data = sub_info.get("data", {})
                        children = sub_data.get("children", {})
                    except Exception as e:
                        logger.warning("解析子目录 %s 失败: %s", node_name, e)
                        children = {}

                if children:
                    children_list = children.values() if isinstance(children, dict) else children
                    for child in children_list:
                        walk(child, folder_path)

        walk(data, "")
        return items
