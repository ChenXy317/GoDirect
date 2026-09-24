import os
import subprocess
from pathlib import Path

from .config import load_config


class IDMInterop:
    """IDM 外部下载器联动与任务导出助手"""

    @staticmethod
    def find_idm_path() -> str | None:
        """寻找本机安装或运行中的 IDMan.exe 路径"""
        try:
            cmd = "Get-Process -Name IDMan -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -First 1"
            proc = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True,
                text=True,
                check=False,
            )
            path = proc.stdout.strip()
            if path and os.path.exists(path):
                return path
        except Exception:
            pass

        candidates = [
            r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe",
            r"C:\Program Files\Internet Download Manager\IDMan.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

        return None

    @classmethod
    def send_to_idm(cls, items: list[dict[str, str]], download_dir: str) -> bool:
        """将下载任务推送至本地运行的 IDM 队列"""
        idm_exe = cls.find_idm_path()
        if not idm_exe:
            return False

        target_dir = Path(download_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        for item in items:
            url = item.get("link", "")
            name = item.get("name", "")
            if not url:
                continue

            cmd = [
                idm_exe,
                "/d",
                url,
                "/p",
                str(target_dir),
                "/f",
                name,
                "/a",
            ]
            try:
                subprocess.Popen(cmd)
            except Exception:
                pass
        return True

    @classmethod
    def generate_curl_script(
        cls, items: list[dict[str, str]], token: str, output_path: str
    ) -> str:
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
        with open(output_path, "w", encoding="utf-8-sig") as f:
            f.write(content)

        return output_path
