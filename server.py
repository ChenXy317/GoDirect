import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import load_config, update_config
from core.downloader import DownloadManager
from core.gofile_api import GofileAPI
from core.idm_interop import IDMInterop

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("server")

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

api_client = GofileAPI()
manager = DownloadManager()
active_websockets: list[WebSocket] = []


async def broadcast_task_metrics():
    """定期向所有前端 WebSocket 客户端广播任务进度和瞬时网速"""
    while True:
        try:
            if active_websockets:
                tasks_data = manager.get_all_tasks()
                msg = {"type": "metrics", "tasks": tasks_data}
                for ws in list(active_websockets):
                    try:
                        await ws.send_json(msg)
                    except Exception:
                        if ws in active_websockets:
                            active_websockets.remove(ws)
        except Exception as e:
            logger.error("广播任务进度异常: %s", e)
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理与后台任务调度"""
    manager.set_event_loop(asyncio.get_event_loop())
    broadcast_task = asyncio.create_task(broadcast_task_metrics())
    yield
    broadcast_task.cancel()


app = FastAPI(title="GoDirect - 简易下载管理器", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ParseRequest(BaseModel):
    url: str
    password: str | None = None


class DownloadItem(BaseModel):
    name: str
    link: str
    total_size: int = 0
    relative_path: str | None = ""


class StartDownloadRequest(BaseModel):
    items: list[DownloadItem]
    token: str | None = None


class TaskActionRequest(BaseModel):
    task_id: str


class SettingsUpdateRequest(BaseModel):
    download_dir: str | None = None
    account_token: str | None = None
    proxy: str | None = None
    chunk_threads: int | None = None
    max_concurrent_tasks: int | None = None


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """前端实时状态同步 WebSocket 接口"""
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        await websocket.send_json({"type": "metrics", "tasks": manager.get_all_tasks()})
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.warning("WebSocket 连接异常: %s", e)
    finally:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


@app.get("/")
def get_index():
    """返回前端主页"""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="前端页面尚未构建")
    return FileResponse(str(index_file))


@app.post("/api/parse")
def parse_gofile_url(req: ParseRequest):
    """解析 Gofile 分享链接并提取文件清单"""
    if not req.url:
        raise HTTPException(status_code=400, detail="请输入有效的 Gofile 链接")

    try:
        items = api_client.collect_all_download_items(req.url, req.password)
        if items and items[0].get("status") == "password_required":
            return {"status": "password_required", "message": "该内容受密码保护，请输入提取密码"}

        token = api_client.ensure_account()
        return {
            "status": "ok",
            "count": len(items),
            "items": items,
            "token": token,
        }
    except Exception as e:
        logger.error("解析链接失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download/start")
def start_downloads(req: StartDownloadRequest):
    """批量将解析到的文件加入下载队列并开始下载"""
    if not req.items:
        raise HTTPException(status_code=400, detail="下载清单为空")

    token = req.token or api_client.ensure_account()
    added_tasks = []
    for item in req.items:
        task = manager.add_task(
            name=item.name,
            url=item.link,
            token=token,
            relative_path=item.relative_path or item.name,
            total_size=item.total_size,
        )
        added_tasks.append(task.to_dict())

    return {"status": "ok", "added": len(added_tasks), "tasks": added_tasks}


@app.post("/api/download/pause")
def pause_task(req: TaskActionRequest):
    """暂停指定下载任务"""
    success = manager.pause_task(req.task_id)
    return {"status": "ok" if success else "failed"}


@app.post("/api/download/resume")
def resume_task(req: TaskActionRequest):
    """恢复指定下载任务"""
    success = manager.resume_task(req.task_id)
    return {"status": "ok" if success else "failed"}


@app.post("/api/download/cancel")
def cancel_task(req: TaskActionRequest):
    """取消并移除下载任务"""
    success = manager.cancel_task(req.task_id)
    return {"status": "ok" if success else "failed"}


@app.post("/api/download/open-folder")
def open_download_folder():
    """在 Windows 资源管理器中打开默认下载目录"""
    cfg = load_config()
    d_dir = Path(cfg.get("download_dir", "./downloads")).resolve()
    d_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "startfile"):
        os.startfile(str(d_dir))
    return {"status": "ok", "path": str(d_dir)}


@app.get("/api/settings")
def get_settings():
    """读取当前应用配置项"""
    return {"status": "ok", "config": load_config()}


@app.post("/api/settings")
def save_settings(req: SettingsUpdateRequest):
    """保存更新的应用配置项并刷新服务会话"""
    updates = req.dict(exclude_unset=True)
    updated = update_config(updates)
    api_client._init_session()
    return {"status": "ok", "config": updated}


@app.post("/api/export/curl")
def export_curl_script(req: StartDownloadRequest):
    """导出为一键下载的 Windows 批处理脚本"""
    cfg = load_config()
    base_dir = Path(cfg.get("download_dir", "./downloads")).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    bat_path = base_dir / "download_gofile.bat"

    token = req.token or api_client.ensure_account()
    items_dict = [item.dict() for item in req.items]
    IDMInterop.generate_curl_script(items_dict, token, str(bat_path))
    return {"status": "ok", "bat_path": str(bat_path)}


@app.post("/api/export/idm")
def push_to_idm(req: StartDownloadRequest):
    """推送任务列表至本地 IDM 队列"""
    cfg = load_config()
    download_dir = cfg.get("download_dir", "./downloads")
    items_dict = [item.dict() for item in req.items]
    token = req.token or api_client.ensure_account()
    success = IDMInterop.send_to_idm(items_dict, download_dir, token=token)
    if not success:
        raise HTTPException(status_code=404, detail="未在系统中检测到可用的 IDMan.exe 程序或投递任务失败")
    return {"status": "ok", "pushed": len(items_dict)}
