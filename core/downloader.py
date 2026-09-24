import asyncio
import json
import logging
import math
import os
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
    """清理文件名中的非法字符以适配本地文件系统"""
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return cleaned if cleaned else "unnamed_file"


class TaskStatus(str, Enum):
    PENDING = "pending"
    CONNECTING = "connecting"
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
        relative_path: str = "",
        total_size: int = 0,
    ):
        self.task_id = task_id
        self.name = name
        self.url = url
        self.token = token
        self.save_path = save_path
        self.relative_path = relative_path
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
        self._lock = threading.Lock()

    def to_dict(self) -> dict[str, Any]:
        """序列化为前端适用的字典数据"""
        percent = 0.0
        if self.total_size > 0:
            percent = round((self.downloaded_size / self.total_size) * 100, 1)

        now = time.time()
        display_speed = self.speed
        if self.status == TaskStatus.DOWNLOADING and (now - self._last_tick_time > 2.5):
            display_speed = 0

        return {
            "task_id": self.task_id,
            "name": self.name,
            "url": self.url,
            "save_path": self.save_path,
            "relative_path": self.relative_path,
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
        """计算瞬时下载速度与预计剩余时间"""
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
    """基于 HTTP Range 的多任务与多线程分块下载引擎"""

    def __init__(self):
        self.tasks: dict[str, DownloadTask] = {}
        self.task_executor = ThreadPoolExecutor(max_workers=8)
        self.chunk_executor = ThreadPoolExecutor(max_workers=32)
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
            relative_path=relative_path or clean_name,
            total_size=total_size,
        )
        self.tasks[task_id] = task
        self._schedule_worker(task)
        return task

    def pause_task(self, task_id: str) -> bool:
        """暂停指定下载任务"""
        task = self.tasks.get(task_id)
        if task and task.status in (TaskStatus.DOWNLOADING, TaskStatus.CONNECTING, TaskStatus.PENDING):
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
        """取消并移除下载任务，清理未完成的临时文件"""
        task = self.tasks.get(task_id)
        if task:
            task._stop_requested = True
            task.status = TaskStatus.CANCELED
            temp_file = Path(task.save_path + ".part")
            meta_file = Path(task.save_path + ".part.meta")
            for f in (temp_file, meta_file):
                if f.exists():
                    try:
                        f.unlink()
                    except Exception:
                        pass
            del self.tasks[task_id]
            return True
        return False

    def get_all_tasks(self) -> list[dict[str, Any]]:
        """获取所有任务的运行状态列表"""
        return [t.to_dict() for t in list(self.tasks.values())]

    def _schedule_worker(self, task: DownloadTask) -> None:
        """提交任务至后台线程池调度执行"""
        self.task_executor.submit(self._download_worker, task)

    def _download_worker(self, task: DownloadTask) -> None:
        """下载工作线程主调度逻辑"""
        while not self._semaphore.acquire(timeout=0.2):
            if task._stop_requested or task.status == TaskStatus.CANCELED:
                return

        cfg = load_config()
        proxy = cfg.get("proxy", "").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        user_agent = cfg.get("user_agent", "")
        chunk_threads_cfg = max(1, min(int(cfg.get("chunk_threads", 4)), 16))

        save_path = Path(task.save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(str(save_path) + ".part")
        meta_path = Path(str(save_path) + ".part.meta")

        try:
            if task._stop_requested or task.status == TaskStatus.CANCELED:
                return

            task.status = TaskStatus.CONNECTING
            task._stop_requested = False

            base_headers = {
                "Cookie": f"accountToken={task.token}",
                "Referer": "https://gofile.io/",
                "User-Agent": user_agent,
            }

            # 探测服务端是否支持 Range 并获取精确大小
            probe_headers = dict(base_headers)
            probe_headers["Range"] = "bytes=0-0"
            server_supports_range = False
            total_size = task.total_size

            try:
                with requests.get(
                    task.url,
                    headers=probe_headers,
                    proxies=proxies,
                    timeout=20,
                    stream=True,
                ) as probe_resp:
                    if probe_resp.status_code in (301, 302, 303, 307):
                        raise RuntimeError("直链鉴权失效，被服务端 302 重定向至网页")

                    if probe_resp.status_code == 206:
                        server_supports_range = True
                        cr = probe_resp.headers.get("Content-Range", "")
                        if "/" in cr:
                            parts = cr.split("/")
                            if len(parts) == 2 and parts[1].isdigit():
                                total_size = int(parts[1])
                    elif probe_resp.status_code == 200:
                        cl = probe_resp.headers.get("Content-Length")
                        if cl and cl.isdigit():
                            total_size = int(cl)
                        if probe_resp.headers.get("Accept-Ranges") == "bytes":
                            server_supports_range = True
                    else:
                        raise RuntimeError(f"服务器响应状态异常: HTTP {probe_resp.status_code}")
            except Exception as e:
                raise RuntimeError(f"连接下载源失败: {e}")

            task.total_size = total_size

            # 如果已存在完整的最终文件且大小匹配，直接标记完成
            if save_path.exists() and total_size > 0 and save_path.stat().st_size == total_size:
                task.downloaded_size = total_size
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                task.speed = 0
                return

            # 多线程分块并发下载流程
            if server_supports_range and total_size >= 2 * 1024 * 1024:
                self._run_chunked_download(
                    task=task,
                    total_size=total_size,
                    threads=chunk_threads_cfg,
                    temp_path=temp_path,
                    meta_path=meta_path,
                    base_headers=base_headers,
                    proxies=proxies,
                )
            else:
                self._run_single_stream_download(
                    task=task,
                    total_size=total_size,
                    temp_path=temp_path,
                    base_headers=base_headers,
                    proxies=proxies,
                )

            if task._stop_requested or task.status == TaskStatus.CANCELED:
                task.speed = 0
                return

            if task.total_size > 0 and task.downloaded_size < task.total_size:
                raise RuntimeError(
                    f"传输未完成: 实际接收 {task.downloaded_size} 字节，总大小 {task.total_size} 字节"
                )

            if temp_path.exists():
                if save_path.exists():
                    save_path.unlink()
                temp_path.rename(save_path)

            if meta_path.exists():
                try:
                    meta_path.unlink()
                except Exception:
                    pass

            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.speed = 0
            task.eta = 0

        except Exception as e:
            if not task._stop_requested and task.status != TaskStatus.CANCELED:
                logger.error("任务 %s 下载异常: %s", task.name, e)
                task.status = TaskStatus.ERROR
                task.error_message = str(e)
                task.speed = 0
        finally:
            self._semaphore.release()
            if task.status == TaskStatus.CANCELED:
                for f in (temp_path, meta_path):
                    if f.exists():
                        try:
                            f.unlink()
                        except Exception:
                            pass

    def _run_chunked_download(
        self,
        task: DownloadTask,
        total_size: int,
        threads: int,
        temp_path: Path,
        meta_path: Path,
        base_headers: dict[str, str],
        proxies: dict[str, str] | None,
    ) -> None:
        """执行多线程分块并发下载"""
        chunks: list[dict[str, int]] = []
        chunk_size = math.ceil(total_size / threads)

        # 读取断点续传元数据
        if temp_path.exists() and meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    loaded_chunks = json.load(f)
                if isinstance(loaded_chunks, list) and len(loaded_chunks) == threads:
                    chunks = loaded_chunks
            except Exception:
                chunks = []

        if not chunks:
            for i in range(threads):
                s = i * chunk_size
                e = min((i + 1) * chunk_size - 1, total_size - 1)
                chunks.append({"start": s, "end": e, "downloaded": 0})

            # 初始化预分配文件
            with open(temp_path, "wb") as f:
                if total_size > 0:
                    f.truncate(total_size)
            self._save_meta(meta_path, chunks)

        task.downloaded_size = sum(c["downloaded"] for c in chunks)
        task.status = TaskStatus.DOWNLOADING
        task._last_tick_time = time.time()
        task._last_tick_bytes = task.downloaded_size

        meta_lock = threading.Lock()
        active_error: list[str] = []

        def worker(idx: int):
            c = chunks[idx]
            current_start = c["start"] + c["downloaded"]
            current_end = c["end"]
            if current_start > current_end:
                return

            req_headers = dict(base_headers)
            max_retries = 3

            for attempt in range(max_retries):
                if task._stop_requested or task.status == TaskStatus.CANCELED or active_error:
                    return

                req_headers["Range"] = f"bytes={current_start}-{current_end}"
                try:
                    with requests.get(
                        task.url,
                        headers=req_headers,
                        proxies=proxies,
                        timeout=30,
                        stream=True,
                    ) as resp:
                        if resp.status_code not in (200, 206):
                            raise RuntimeError(f"HTTP {resp.status_code}")

                        with open(temp_path, "r+b") as f:
                            f.seek(current_start)
                            for block in resp.iter_content(chunk_size=65536):
                                if task._stop_requested or task.status == TaskStatus.CANCELED:
                                    return

                                if block:
                                    f.write(block)
                                    block_len = len(block)
                                    current_start += block_len
                                    c["downloaded"] += block_len

                                    with task._lock:
                                        task.downloaded_size += block_len
                                        task.update_metrics()

                    # 该分块成功下载完毕
                    break
                except Exception as ex:
                    if attempt < max_retries - 1 and not task._stop_requested:
                        time.sleep(1.0)
                        continue
                    if not task._stop_requested:
                        active_error.append(f"分块 {idx+1} 下载失败: {ex}")
                        return

        # 启动分块并发
        futures = [self.chunk_executor.submit(worker, i) for i in range(threads)]

        while not all(f.done() for f in futures):
            if task._stop_requested or task.status == TaskStatus.CANCELED:
                break
            time.sleep(0.5)
            with meta_lock:
                self._save_meta(meta_path, chunks)

        for f in futures:
            try:
                f.result()
            except Exception as e:
                active_error.append(str(e))

        with meta_lock:
            self._save_meta(meta_path, chunks)

        if active_error and not task._stop_requested:
            raise RuntimeError(active_error[0])

    def _run_single_stream_download(
        self,
        task: DownloadTask,
        total_size: int,
        temp_path: Path,
        base_headers: dict[str, str],
        proxies: dict[str, str] | None,
    ) -> None:
        """执行单连接流式断点续传下载"""
        initial_bytes = 0
        if temp_path.exists():
            initial_bytes = temp_path.stat().st_size
            task.downloaded_size = initial_bytes

        headers = dict(base_headers)
        if initial_bytes > 0:
            headers["Range"] = f"bytes={initial_bytes}-"

        task.status = TaskStatus.DOWNLOADING
        task._last_tick_time = time.time()
        task._last_tick_bytes = initial_bytes

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

            mode = "ab" if initial_bytes > 0 and resp.status_code == 206 else "wb"
            if mode == "wb":
                task.downloaded_size = 0

            with open(temp_path, mode) as f:
                for block in resp.iter_content(chunk_size=65536):
                    if task._stop_requested or task.status == TaskStatus.CANCELED:
                        task.speed = 0
                        return

                    if block:
                        f.write(block)
                        task.downloaded_size += len(block)
                        task.update_metrics()

    @staticmethod
    def _save_meta(meta_path: Path, chunks: list[dict[str, int]]) -> None:
        """原子写入分块断点续传元数据"""
        try:
            tmp = meta_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(chunks, f)
            if tmp.exists():
                tmp.replace(meta_path)
        except Exception:
            pass
