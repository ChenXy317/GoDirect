import socket
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


def open_browser_delayed(url: str, port: int, max_wait: float = 3.0) -> None:
    """等待服务端口监听就绪后在默认浏览器中打开下载管理器界面"""
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.2)
    time.sleep(0.2)
    webbrowser.open(url)


if __name__ == "__main__":
    port = find_free_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    print("=" * 60)
    print(" Gofile Fetch - 简易下载管理器已就绪")
    print(f" 服务访问地址: {server_url}")
    print(" 正在打开浏览器...")
    print("=" * 60)

    threading.Thread(target=open_browser_delayed, args=(server_url, port), daemon=True).start()
    uvicorn.run("server:app", host="127.0.0.1", port=port, log_level="info")
