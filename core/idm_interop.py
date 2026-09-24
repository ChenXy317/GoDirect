import base64
import json
import logging
import os
import subprocess
import winreg
from pathlib import Path
from typing import Any

from .config import load_config

logger = logging.getLogger("idm_interop")

IDM_CS_CODE = r"""
using System;
using System.Runtime.InteropServices;

namespace IDMLib
{
    [ComImport]
    [Guid("94D09862-1875-4FC9-B434-91CF25C840A1")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface ICIDMLinkTransmitter2
    {
        void SendLinkToIDM(string bstrUrl, string bstrReferer, string bstrCookies, string bstrData, string bstrUser, string bstrPassword, string bstrLocalPath, string bstrLocalFileName, int lFlags);
        void SendLinkToIDM2(string bstrUrl, string bstrReferer, string bstrCookies, string bstrData, string bstrUser, string bstrPassword, string bstrLocalPath, string bstrLocalFileName, int lFlags, object reserved1, object reserved2);
    }

    [ComImport]
    [Guid("AC746233-E9D3-49CD-862F-068F7B7CCCA4")]
    public class CIDMLinkTransmitter {}

    public class IDMHelper
    {
        public static bool Send(string url, string referer, string cookies, string path, string filename, int flags)
        {
            try
            {
                var idm = (ICIDMLinkTransmitter2)new CIDMLinkTransmitter();
                idm.SendLinkToIDM2(url, referer, cookies, "", "", "", path, filename, flags, null, null);
                return true;
            }
            catch
            {
                return false;
            }
        }
    }
}
"""


class IDMInterop:
    """IDM 外部下载器联动与任务导出助手"""

    @staticmethod
    def find_idm_path() -> str | None:
        """寻找本机安装或运行中的 IDMan.exe 路径"""
        reg_keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\DownloadManager"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\DownloadManager"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\DownloadManager"),
        ]
        for root, subkey in reg_keys:
            try:
                with winreg.OpenKey(root, subkey) as k:
                    val, _ = winreg.QueryValueEx(k, "ExePath")
                    if val and os.path.exists(val):
                        return val
            except OSError:
                pass

        try:
            cmd = "Get-Process -Name IDMan -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -First 1"
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
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
            r"D:\Program Files (x86)\Internet Download Manager\IDMan.exe",
            r"D:\Program Files\Internet Download Manager\IDMan.exe",
            r"D:\工具\Internet Download Manager\IDMan.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

        return None

    @classmethod
    def send_to_idm(
        cls,
        items: list[dict[str, Any]],
        download_dir: str,
        token: str = "",
        auto_start: bool = False,
    ) -> bool:
        """向本地 IDM 发送携带完整鉴权凭据的下载任务"""
        idm_exe = cls.find_idm_path()
        if not idm_exe:
            return False

        target_dir = Path(download_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        cfg = load_config()
        effective_token = token or cfg.get("account_token", "")
        cookies = f"accountToken={effective_token}" if effective_token else ""
        referer = "https://gofile.io/"
        flags = 0 if auto_start else 2

        payload = []
        for item in items:
            url = item.get("link", "")
            name = item.get("name", "")
            rel_path = item.get("relative_path", "")
            if not url or not name:
                continue

            save_folder = target_dir
            if rel_path:
                rel_parts = [p for p in Path(rel_path).parts if p not in ("..", ".", "/", "\\")]
                if len(rel_parts) > 1:
                    sub_dir = Path(*rel_parts[:-1])
                    candidate_folder = (target_dir / sub_dir).resolve()
                    try:
                        candidate_folder.relative_to(target_dir)
                        save_folder = candidate_folder
                    except ValueError:
                        save_folder = target_dir
                    save_folder.mkdir(parents=True, exist_ok=True)

            payload.append({
                "url": url,
                "name": name,
                "folder": str(save_folder),
            })

        if not payload:
            return False

        b64_payload = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")

        ps_script = f"""
$code = @'
{IDM_CS_CODE}
'@
$jsonBytes = [System.Convert]::FromBase64String('{b64_payload}')
$jsonText = [System.Text.Encoding]::UTF8.GetString($jsonBytes)
$items = $jsonText | ConvertFrom-Json

try {{
    Add-Type -TypeDefinition $code -ErrorAction Stop
    $allSuccess = $true
    foreach ($item in $items) {{
        $ok = [IDMLib.IDMHelper]::Send($item.url, "{referer}", "{cookies}", $item.folder, $item.name, {flags})
        if (-not $ok) {{ $allSuccess = $false }}
    }}
    if ($allSuccess) {{ Write-Output "OK"; exit }}
}} catch {{}}

foreach ($item in $items) {{
    Start-Process -FilePath "{idm_exe}" -ArgumentList "/d", "`"$($item.url)`"", "/p", "`"$($item.folder)`"", "/f", "`"$($item.name)`"", "/a"
}}
Write-Output "OK"
"""
        encoded_cmd = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_cmd],
                capture_output=True,
                text=True,
                check=False,
            )
            return "OK" in proc.stdout
        except Exception as e:
            logger.error("向 IDM 投递任务失败: %s", e)
            return False

    @classmethod
    def generate_curl_script(
        cls, items: list[dict[str, Any]], token: str, output_path: str
    ) -> str:
        """生成带完整鉴权 Cookie 和 Referer 的 Windows 批处理下载脚本"""
        cfg = load_config()
        ua = cfg.get("user_agent", "").replace("%", "%%")
        proxy = cfg.get("proxy", "").strip()

        lines = [
            "@echo off",
            "chcp 65001 >nul",
            "echo 正在通过 cURL 高速下载 Gofile 资源...",
            f"set TOKEN={token}",
            f"set UA={ua}",
        ]

        if proxy:
            lines.append(f"set PROXY={proxy.replace('%', '%%')}")
            proxy_flag = '-x "%PROXY%" '
        else:
            proxy_flag = ""

        lines.append("")

        for item in items:
            name = item.get("name", "downloaded_file")
            url = item.get("link", "")
            rel_path = item.get("relative_path", "") or name
            if not url:
                continue

            escaped_url = url.replace("%", "%%")
            escaped_name = name.replace("%", "%%").replace('"', "")
            clean_rel_path = rel_path.replace("\\", "/").replace("%", "%%").replace('"', "")

            lines.append(f'echo 开始下载: {escaped_name}')
            lines.append(
                f'curl.exe -L -C - --create-dirs {proxy_flag}-o "{clean_rel_path}" "{escaped_url}" '
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
