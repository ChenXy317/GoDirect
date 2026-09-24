import asyncio
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import requests

from .config import load_config

logger = logging.getLogger("downloader")


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符以适配 Windows 文件系统"""
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return cleaned if cleaned else "unnamed_file"


class TaskStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELED = "canceled"


class DownloadTask:
    """单个下载任务模型与运行时状态"""

    def __init__(
        self,
        task_id: str,
        name: str,
        url: str,
        token: str,
        save_path: str,
        total_size: int = 0,
    ):
        self.task_id = task_id
        self.name = name
        self.url = url
        self.token = token
        self.save_path = save_path
        self.total_size = total_size
        self.downloaded_size = 0
        self.status = TaskStatus.PENDING
        self.speed = 0
        self.eta = 0
        self.error_message = ""
        self.created_at = time.time()
        self.completed_at: float | None = None

        self._stop_requested = False
        self._last_tick_time = time.time()
        self._last_tick_bytes = 0

    def to_dict(self) -> dict[str, Any]:
        """序列化为前端适用的字典数据"""
        percent = 0.0
        if self.total_size > 0:
            percent = round((self.downloaded_size / self.total_size) * 100, 1)

        # 超时未接收到新数据则重置瞬时速度显示
        now = time.time()
        display_speed = self.speed
        if self.status == TaskStatus.DOWNLOADING and (now - self._last_tick_time > 2.0):
            display_speed = 0

        return {
            "task_id": self.task_id,
            "name": self.name,
            "url": self.url,
            "save_path": self.save_path,
            "total_size": self.total_size,
            "downloaded_size": self.downloaded_size,
            "percent": percent,
            "status": self.status.value,
            "speed": display_speed,
            "eta": self.eta,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    def update_metrics(self) -> None:
        """根据增量字节与时间计算瞬时速度与预计剩余时间"""
        now = time.time()
        dt = now - self._last_tick_time
        if dt >= 0.5:
            d_bytes = self.downloaded_size - self._last_tick_bytes
            self.speed = int(d_bytes / dt) if dt > 0 else 0
            if self.speed > 0 and self.total_size > self.downloaded_size:
                self.eta = int((self.total_size - self.downloaded_size) / self.speed)
            else:
                self.eta = 0
            self._last_tick_time = now
            self._last_tick_bytes = self.downloaded_size


class DownloadManager:
    """多任务并发与断点续传管理引擎"""

    def __init__(self):
        self.tasks: dict[str, DownloadTask] = {}
        self.executor = ThreadPoolExecutor(max_workers=16)
        self.on_update: Callable[[], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        cfg = load_config()
        max_concurrent = max(1, int(cfg.get("max_concurrent_tasks", 3)))
        self._semaphore = threading.Semaphore(max_concurrent)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置异步事件循环以支持状态回调"""
        self._loop = loop

    def add_task(
        self,
        name: str,
        url: str,
        token: str,
        relative_path: str = "",
        total_size: int = 0,
    ) -> DownloadTask:
        """创建并注册一个下载任务"""
        cfg = load_config()
        base_dir = Path(cfg.get("download_dir", "./downloads")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)

        clean_name = sanitize_filename(name)
        if relative_path:
            rel_parts = [
                sanitize_filename(p)
                for p in Path(relative_path).parts
                if p not in ("..", ".", "/", "\\")
            ]
            rel_path_clean = Path(*rel_parts)
            target_path = (base_dir / rel_path_clean).resolve()
        else:
            target_path = (base_dir / clean_name).resolve()

        try:
            target_path.relative_to(base_dir)
        except ValueError:
            target_path = base_dir / clean_name

        task_id = str(uuid.uuid4())[:8]
        task = DownloadTask(
            task_id=task_id,
            name=clean_name,
            url=url,
            token=token,
            save_path=str(target_path),
            total_size=total_size,
        )
        self.tasks[task_id] = task
        self._schedule_worker(task)
        return task

    def pause_task(self, task_id: str) -> bool:
        """暂停指定下载任务"""
        task = self.tasks.get(task_id)
        if task and task.status in (TaskStatus.DOWNLOADING, TaskStatus.PENDING):
            task._stop_requested = True
            task.status = TaskStatus.PAUSED
            task.speed = 0
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        """恢复指定下载任务"""
        task = self.tasks.get(task_id)
        if task and task.status in (TaskStatus.PAUSED, TaskStatus.ERROR):
            task._stop_requested = False
            task.status = TaskStatus.PENDING
            task.error_message = ""
            self._schedule_worker(task)
            return True
        return False

    def cancel_task(self, task_id: str) -> bool:
        """取消并移除下载任务"""
        task = self.tasks.get(task_id)
        if task:
            task._stop_requested = True
            task.status = TaskStatus.CANCELED
            temp_file = Path(task.save_path + ".part")
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except PermissionError:
                    pass
                except Exception as e:
                    logger.warning("清理临时文件失败: %s", e)
            del self.tasks[task_id]
            return True
        return False

    def get_all_tasks(self) -> list[dict[str, Any]]:
        """获取所有任务的运行状态列表"""
        return [t.to_dict() for t in list(self.tasks.values())]

    def _schedule_worker(self, task: DownloadTask) -> None:
        """提交任务至后台线程池调度执行"""
        self.executor.submit(self._download_worker, task)

    def _download_worker(self, task: DownloadTask) -> None:
        """下载工作线程主逻辑"""
        while not self._semaphore.acquire(timeout=0.2):
            if task._stop_requested or task.status == TaskStatus.CANCELED:
                return

        cfg = load_config()
        proxy = cfg.get("proxy", "").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        save_path = Path(task.save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(str(save_path) + ".part")

        try:
            if task._stop_requested or task.status == TaskStatus.CANCELED:
                return

            initial_bytes = 0
            if temp_path.exists():
                initial_bytes = temp_path.stat().st_size
                task.downloaded_size = initial_bytes

            headers = {
                "Cookie": f"accountToken={task.token}",
                "Referer": "https://gofile.io/",
                "User-Agent": cfg.get("user_agent", ""),
            }
            if initial_bytes > 0:
                headers["Range"] = f"bytes={initial_bytes}-"

            task.status = TaskStatus.DOWNLOADING
            task._stop_requested = False

            with requests.get(
                task.url,
                headers=headers,
                proxies=proxies,
                stream=True,
                timeout=30,
            ) as resp:
                if resp.status_code in (301, 302, 303, 307):
                    raise RuntimeError("下载直链鉴权失败，服务器发生 302 重定向")

                if resp.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP 响应状态异常: {resp.status_code}")

                if "Content-Range" in resp.headers:
                    parts = resp.headers["Content-Range"].split("/")
                    if len(parts) == 2 and parts[1].isdigit():
                        task.total_size = int(parts[1])
                elif "Content-Length" in resp.headers:
                    cl = int(resp.headers["Content-Length"])
                    task.total_size = initial_bytes + cl

                mode = "ab" if initial_bytes > 0 and resp.status_code == 206 else "wb"
                if mode == "wb":
                    task.downloaded_size = 0

                with open(temp_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if task._stop_requested or task.status == TaskStatus.CANCELED:
                            task.speed = 0
                            return

                        if chunk:
                            f.write(chunk)
                            task.downloaded_size += len(chunk)
                            task.update_metrics()

            if task._stop_requested or task.status == TaskStatus.CANCELED:
                task.speed = 0
                return

            if task.total_size > 0 and task.downloaded_size < task.total_size:
                raise RuntimeError(
                    f"传输意外中断，实际接收 {task.downloaded_size} 字节，总大小为 {task.total_size} 字节"
                )

            if temp_path.exists():
                if save_path.exists():
                    save_path.unlink()
                temp_path.rename(save_path)

            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.speed = 0
            task.eta = 0

        except Exception as e:
            if not task._stop_requested and task.status != TaskStatus.CANCELED:
                logger.error("任务 %s 下载失败: %s", task.name, e)
                task.status = TaskStatus.ERROR
                task.error_message = str(e)
                task.speed = 0
        finally:
            self._semaphore.release()
            if task.status == TaskStatus.CANCELED:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except Exception:
                    pass
