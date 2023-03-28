import json
import logging
import threading
import collections
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue, Empty

from .base import BaseBroker, BaseConsumer
from ..message import Message

logger = logging.getLogger(__name__)

Server = collections.namedtuple("Server", ["path", "queue"])


class WebHookServer(BaseHTTPRequestHandler):
    servers = collections.defaultdict(list)

    def do_POST(self):
        """
        接收WebHook请求
        """
        server_paths = WebHookServer.servers.get(self.server.server_address, [])
        for server in server_paths:
            if self.path == server.path:
                queue = server.queue
                break
        else:
            return self.send_error(404)

        content_len = int(self.headers.get('content-length', 0))
        post_body = self.rfile.read(content_len).decode("utf-8")
        try:
            post_json = json.loads(post_body).get("body")
        except json.JSONDecodeError:
            self.send_error(400, message="Invalid JSON format")
            return
        queue.put_nowait(json.dumps(post_body))
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{ "status": "ok" }')


class WebHookBroker(BaseBroker):
    _servers = {}

    def __init__(self,
                 path: str,
                 host: str = "0.0.0.0",
                 port: int = 8090,
                 *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = Queue()
        self.host = host
        self.port = port
        self.path = path

        self._create_server()
        logger.debug(f"WebHookBroker: {self.host}:{self.port}{self.path}")

    def _create_server(self):

        if (self.host, self.port) not in self._servers:
            hs = ThreadingHTTPServer(
                (self.host, self.port),
                WebHookServer
            )
            self._servers[(self.host, self.port)] = hs
        else:
            hs = self._servers[(self.host, self.port)]

        WebHookServer.servers[(self.host, self.port)].append(Server(self.path, self.queue))
        threading.Thread(target=hs.serve_forever).start()

    def consume(self, *args, **kwargs):
        return WebHookConsumer(self.queue, *args, **kwargs)


class WebHookConsumer(BaseConsumer):
    def _to_message(self, data: str):
        message = json.loads(data)
        return Message(body=message.get("body"), extra=message.get("extra"), msg=None)
