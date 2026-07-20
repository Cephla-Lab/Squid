import socket
import time

import httpx

from squid_service.config import ServiceConfig
from squid_service.rest.app import create_app
from squid_service.rest.server import CoreServiceServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_start_serve_stop():
    # /v1/healthz touches neither service nor hardware, a bare object suffices
    app = create_app(service=object(), config=ServiceConfig())
    port = _free_port()
    server = CoreServiceServer(app, "127.0.0.1", port)
    server.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/v1/healthz", timeout=1.0)
                if r.status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        assert r.json() == {"alive": True}
        assert server.is_running()
    finally:
        server.stop()
    assert not server.is_running()
