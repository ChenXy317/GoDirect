import socket
import sys
import threading
import time
import webbrowser
import uvicorn


def find_free_port(default_port: int = 8000) -> int:
    """寻找本地空闲端口，若默认端口被占用则顺延"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", default_port))
        sock.close()
        return default_port
    except OSError:
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port


def open_browser_delayed(url: str, delay: float = 1.0) -> None:
    """延时自动在默认浏览器中打开下载管理器界面"""
    time.sleep(delay)
    webbrowser.open(url)


if __name__ == "__main__":
    port = find_free_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    print("=" * 60)
    print(" Gofile Fetch - 简易下载管理器已就绪")
    print(f" 服务访问地址: {server_url}")
    print(" 正在打开浏览器...")
    print("=" * 60)

    # 启动后台线程唤起浏览器
    threading.Thread(target=open_browser_delayed, args=(server_url,), daemon=True).start()

    # 启动 FastAPI 服务
    uvicorn.run("server:app", host="127.0.0.1", port=port, log_level="info")
