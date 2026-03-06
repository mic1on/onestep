import logging
import threading
import collections
from typing import Dict, List, DefaultDict, Any, Optional
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .memory import MemoryBroker, MemoryConsumer

logger = logging.getLogger(__name__)

Server = collections.namedtuple("Server", ["path", "queue"])


class WebHookServer(BaseHTTPRequestHandler):
    """WebHook HTTP 请求处理器"""

    servers: DefaultDict[Any, List[Server]] = collections.defaultdict(list)
    api_key: Optional[str] = None  # API 密钥，用于简单认证

    def log_message(self, format, *args):
        """覆盖日志方法，减少控制台输出"""
        logger.debug(f"WebHook: {format % args}")

    def do_POST(self):
        """
        接收 WebHook 请求

        支持 API key 认证（通过 X-API-Key 请求头）
        """
        # 检查 API key（如果配置了）
        if self.api_key:
            provided_key = self.headers.get('X-API-Key')
            if provided_key != self.api_key:
                logger.warning("WebHook 请求未授权：无效的 API key")
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{ "error": "Unauthorized" }')
                return

        server_paths = WebHookServer.servers.get(self.server.server_address, [])
        for server in server_paths:
            if self.path == server.path:
                queue = server.queue
                break
        else:
            return self.send_error(404)

        content_len = int(self.headers.get('content-length', 0))
        post_body = self.rfile.read(content_len).decode("utf-8")
        queue.put_nowait(post_body)
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{ "status": "ok" }')


class WebHookBroker(MemoryBroker):
    """WebHook Broker，通过 HTTP 接收消息"""

    _servers: Dict[tuple, ThreadingHTTPServer] = {}
    _lock = threading.Lock()  # 保护 _servers 字典的并发访问

    def __init__(self,
                 path: str,
                 host: str = "127.0.0.1",
                 port: int = 8090,
                 api_key: Optional[str] = None,
                 *args,
                 **kwargs):
        """
        初始化 WebHook Broker

        :param path: WebHook 路径（如 "/webhook"）
        :param host: 监听主机（默认: "127.0.0.1"，仅本地访问）
        :param port: 监听端口（默认: 8090）
        :param api_key: 可选的 API 密钥，用于简单认证
        """
        super().__init__(*args, **kwargs)
        self.host = host
        self.port = port
        self.path = path
        self.api_key = api_key
        self.threads: List[threading.Thread] = []
        WebHookServer.api_key = api_key  # 设置全局 API key

    def _create_server(self):
        """创建并启动 WebHook 服务器"""
        with self._lock:
            if (self.host, self.port) not in self._servers:
                hs = ThreadingHTTPServer(
                    (self.host, self.port),
                    WebHookServer
                )
                self._servers[(self.host, self.port)] = hs
                # 只有在创建新服务器时才启动线程
                thread = threading.Thread(target=hs.serve_forever)
                thread.daemon = True
                thread.start()
                self.threads.append(thread)
                logger.info(f"WebHook 服务器已启动: http://{self.host}:{self.port}")
            else:
                hs = self._servers[(self.host, self.port)]

            # 注册路径
            WebHookServer.servers[(self.host, self.port)].append(Server(self.path, self.queue))
            logger.debug(f"WebHook 路径已注册: {self.host}:{self.port}{self.path}")

    def consume(self, *args, **kwargs):
        self._create_server()
        logger.debug(f"WebHookBroker: {self.host}:{self.port}{self.path}")
        return WebHookConsumer(self, *args, **kwargs)

    def shutdown(self):
        """关闭 WebHook 服务器并清理资源"""
        with self._lock:
            hs = self._servers.get((self.host, self.port))
            if hs:
                hs.shutdown()
                logger.info(f"WebHook 服务器已关闭: {self.host}:{self.port}")

            # 清理服务器记录
            if (self.host, self.port) in self._servers:
                del self._servers[(self.host, self.port)]

            # 清理路径注册
            if (self.host, self.port) in WebHookServer.servers:
                del WebHookServer.servers[(self.host, self.port)]

            # 等待线程结束
            for thread in self.threads:
                thread.join(timeout=5)  # 最多等待 5 秒

            logger.debug(f"WebHook 资源清理完成: {self.host}:{self.port}")


class WebHookConsumer(MemoryConsumer):
    ...
