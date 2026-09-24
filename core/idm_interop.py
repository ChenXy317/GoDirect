import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .config import load_config


class IDMInterop:
    """IDM 外部下载器联动与任务导出助手"""

    @staticmethod
    def find_idm_path() -> Optional[str]:
        """寻找本机安装或运行中的 IDMan.exe 路径"""
        # 1. 尝试从运行中进程获取
        try:
            cmd = "Get-Process -Name IDMan -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -First 1"
            proc = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True)
            path = proc.stdout.strip()
            if path and os.path.exists(path):
                return path
        except Exception:
            pass

        # 2. 检查常见安装位置
        candidates = [
            r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe",
            r"C:\Program Files\Internet Download Manager\IDMan.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

        return None

    @classmethod
    def generate_curl_script(cls, items: List[Dict[str, str]], token: str, output_path: str) -> str:
        """生成带完整鉴权 Cookie 和 Referer 的 Windows 批处理下载脚本"""
        cfg = load_config()
        ua = cfg.get("user_agent", "")
        lines = [
            "@echo off",
            "chcp 65001 >nul",
            "echo 正在通过 cURL 高速下载 Gofile 资源...",
            f"set TOKEN={token}",
            f"set UA={ua}",
            "",
        ]

        for item in items:
            name = item.get("name", "downloaded_file")
            url = item.get("link", "")
            if not url:
                continue
            lines.append(f'echo 开始下载: {name}')
            lines.append(
                f'curl.exe -L -C - -o "{name}" "{url}" '
                f'-H "Cookie: accountToken=%TOKEN%" '
                f'-H "Referer: https://gofile.io/" '
                f'-H "User-Agent: %UA%"'
            )
            lines.append("")

        lines.append("echo 全部下载完成！")
        lines.append("pause")

        content = "\r\n".join(lines)
        with open(output_path, "w", encoding="gbk", errors="ignore") as f:
            f.write(content)

        return output_path
