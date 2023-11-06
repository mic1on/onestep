import logging
import threading
import collections
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .base import BaseLocalBroker, BaseLocalConsumer

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
        queue.put_nowait(post_body)
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{ "status": "ok" }')


class WebHookBroker(BaseLocalBroker):
    _servers = {}

    def __init__(self,
                 path: str,
                 host: str = "0.0.0.0",
                 port: int = 8090,
                 *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.host = host
        self.port = port
        self.path = path
        self.threads = []

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
        thread = threading.Thread(target=hs.serve_forever)
        thread.start()
        self.threads.append(thread)

    def consume(self, *args, **kwargs):
        self._create_server()
        logger.debug(f"WebHookBroker: {self.host}:{self.port}{self.path}")
        return WebHookConsumer(self.queue, *args, **kwargs)

    def shutdown(self):
        hs = self._servers[(self.host, self.port)]
        if hs:
            hs.shutdown()
        for thread in self.threads:
            thread.join()


class WebHookConsumer(BaseLocalConsumer):
    ...
