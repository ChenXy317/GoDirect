import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.downloader import sanitize_filename, DownloadTask, TaskStatus, DownloadManager
from core.gofile_api import GofileAPI
from core.idm_interop import IDMInterop
from fastapi.testclient import TestClient
from server import app


def test_sanitize_filename():
    """测试文件名清理与 Windows 保留设备名称防御"""
    assert sanitize_filename("test/file:name*?.txt") == "test_file_name__.txt"
    assert sanitize_filename("normal_file.mp4") == "normal_file.mp4"
    assert sanitize_filename("con.txt") == "_con.txt"
    assert sanitize_filename("AUX.mp4") == "_AUX.mp4"
    assert sanitize_filename("nul") == "_nul"
    assert sanitize_filename("trailing_dots... ") == "trailing_dots"
    assert sanitize_filename("") == "unnamed_file"


def test_download_task_metrics():
    """测试下载任务状态与指标序列化"""
    task = DownloadTask(
        task_id="test1",
        name="demo.zip",
        url="https://example.com/demo.zip",
        token="test_token",
        save_path="C:/dummy/demo.zip",
        total_size=1000,
    )
    assert task.status == TaskStatus.PENDING
    task.downloaded_size = 500
    data = task.to_dict()
    assert data["percent"] == 50.0
    assert data["task_id"] == "test1"


def test_download_manager_lifecycle(tmp_path):
    """测试下载管理器初始化、并发更新与任务控制"""
    manager = DownloadManager()
    manager.update_settings(max_concurrent_tasks=5)
    assert manager._max_concurrent == 5

    task = manager.add_task(
        name="sample.bin",
        url="https://example.com/sample.bin",
        token="tok123",
        relative_path="sample.bin",
        total_size=1024,
    )
    assert task.task_id in manager.tasks
    assert manager.pause_task(task.task_id) is True
    assert task.status == TaskStatus.PAUSED
    assert manager.cancel_task(task.task_id) is True
    assert task.task_id not in manager.tasks
    manager.shutdown()


def test_gofile_api_helpers():
    """测试 Gofile 签名计算与提取函数"""
    api = GofileAPI()
    assert api.extract_content_id("https://gofile.io/d/abcXYZ") == "abcXYZ"
    assert api.extract_content_id("abcXYZ") == "abcXYZ"

    sample_script = "var _x = '\\x31\\x32\\x61\\x66\\x30\\x35\\x36\\x64\\x61\\x63\\x65\\x61\\x30\\x62';"
    extracted = api.extract_salt_from_script(sample_script)
    assert extracted == "12af056dacea0b"

    wt = api.calculate_wt("dummy_token")
    assert len(wt) == 64


def test_curl_batch_generation(tmp_path):
    """测试 cURL 批处理下载脚本的安全生成与特殊符号转义"""
    items = [
        {"name": "file%20name.zip", "link": "https://srv.gofile.io/dl/file%20name.zip", "relative_path": "folder/file%20name.zip"},
    ]
    bat_file = tmp_path / "download.bat"
    IDMInterop.generate_curl_script(items, "test_token", str(bat_file))

    assert bat_file.exists()
    content = bat_file.read_text(encoding="utf-8-sig")
    assert "chcp 65001" in content
    assert "%%" in content


def test_server_api_settings():
    """测试系统配置查询与热更新接口"""
    client = TestClient(app)
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "config" in data

    post_res = client.post("/api/settings", json={"chunk_threads": 6, "max_concurrent_tasks": 4})
    assert post_res.status_code == 200
    cfg = post_res.json()["config"]
    assert cfg["chunk_threads"] == 6
    assert cfg["max_concurrent_tasks"] == 4


def test_chunk_resume_preservation(tmp_path):
    """测试即使配置线程数发生变动，历史分块元数据仍被正确继承"""
    temp_file = tmp_path / "bigfile.bin.part"
    meta_file = tmp_path / "bigfile.bin.part.meta"

    temp_file.write_bytes(b"\x00" * 2000)
    meta_chunks = [
        {"start": 0, "end": 499, "downloaded": 500},
        {"start": 500, "end": 999, "downloaded": 500},
        {"start": 1000, "end": 1499, "downloaded": 500},
        {"start": 1500, "end": 1999, "downloaded": 0},
    ]
    meta_file.write_text(json.dumps(meta_chunks), encoding="utf-8")

    manager = DownloadManager()
    task = DownloadTask(
        task_id="chunk_test",
        name="bigfile.bin",
        url="http://dummy",
        token="tok",
        save_path=str(tmp_path / "bigfile.bin"),
        total_size=2000,
    )
    task._stop_requested = True

    # 传入期望 8 线程（不同于历史 4 线程），验证是否沿用已有 4 分块而不是重置
    manager._run_chunked_download(
        task=task,
        total_size=2000,
        threads=8,
        temp_path=temp_file,
        meta_path=meta_file,
        base_headers={},
        proxies=None,
    )

    assert task.downloaded_size == 1500
    manager.shutdown()

