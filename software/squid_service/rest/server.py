"""Run the REST app on uvicorn inside a daemon thread of the GUI process."""

import threading
from typing import Optional

import uvicorn

import squid.logging


class CoreServiceServer:
    def __init__(self, app, host: str, port: int):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(self._config)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.is_running():
            return
        self._server = uvicorn.Server(self._config)  # uvicorn servers are single-use
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="SquidCoreService")
        self._thread.start()
        self._log.info(f"Core service REST API on http://{self._config.host}:{self._config.port}")

    def stop(self) -> None:
        if not self.is_running():
            return
        self._server.should_exit = True
        self._thread.join(timeout=5.0)
        self._log.info("Core service REST API stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
