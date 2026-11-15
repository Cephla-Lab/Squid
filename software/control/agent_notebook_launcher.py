"""Start the lab notebook of an agent session from the Squid window (Utils > Agent Notebook).

The notebook is a separate program: a local web page with a chat, a notebook that is written while an agent
operates the microscope, and who is driving. Squid does not import it. Squid has a command line (the setting
agent_notebook_command) and the address of the page (agent_notebook_url). With no command there is no menu entry.

The notebook program says what it is: it answers GET <address>api/service with a JSON object that has
"role": "agent-notebook". Squid opens a page, and starts the control server for it, only when the program at the
address says so.

No Qt in this module: the window gives the functions that start the control server and open a page.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable, List, Optional

import squid.logging

LOG_FILE_NAME = "agent_notebook.log"
SERVICE_PATH = "api/service"  # after the address of the page: what the program is
NOTEBOOK_ROLE = "agent-notebook"
START_WAIT_S = 20.0  # how long the program gets to answer at its page


def default_log_path() -> str:
    return os.path.join(squid.logging.get_default_log_directory(), LOG_FILE_NAME)


def read_log_tail(path: str, lines: int = 8) -> str:
    """The last lines of the notebook program's output, for the message of a failed start."""
    try:
        with open(path, "r", errors="replace") as f:
            return "".join(f.readlines()[-lines:]).strip()
    except OSError:
        return ""


def command_from_setting(setting: str, now: Optional[datetime] = None) -> List[str]:
    """The program and its arguments from the setting. `{timestamp}` in an argument becomes the time of the
    start (YYYYMMDD-HHMMSS), so that each start can have its own folder. An empty setting gives []."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return [part.replace("{timestamp}", stamp) for part in shlex.split(setting, posix=True)]


def notebook_answers(url: str, timeout_s: float = 0.5) -> bool:
    """True when a program answers at the address of the page (with any HTTP status)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def notebook_runs(url: str, timeout_s: float = 0.5) -> bool:
    """True only when the program at the address says that it is the notebook. A different program that answers
    there, and no answer, are both False."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/" + SERVICE_PATH, timeout=timeout_s) as response:
            said = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return isinstance(said, dict) and said.get("role") == NOTEBOOK_ROLE


class AgentNotebook:
    """The notebook as the window uses it: open() makes sure that the page is there and opens it."""

    def __init__(
        self,
        setting: str,
        url: str,
        start_control_server: Callable[[], None],
        open_page: Callable[[str], None],
        log_path: Optional[str] = None,
        wait_s: float = START_WAIT_S,
    ):
        self._setting = setting
        self._url = url
        self._start_control_server = start_control_server
        self._open_page = open_page
        self._log_path = log_path or default_log_path()
        self._wait_s = wait_s
        self.process: Optional[subprocess.Popen] = None  # the program, when this Squid started it
        # close() and the start of the program take turns: a close during a start is either before the program
        # starts (then it does not start) or after (then close() ends it).
        self._lock = threading.Lock()
        self._closed = False

    def open(self) -> Optional[str]:
        """Open the page of the notebook. Squid starts the control server each time (the agent of the notebook
        talks to it, and a notebook that someone started by hand can run while the server is off). A notebook that
        runs already is then opened. Else Squid starts the program, and waits until it says that it is the notebook.

        Returns None, or the reason why there is no page (for a message to the user). It can take wait_s: do not
        call it on the GUI thread."""
        runs = notebook_runs(self._url)
        command = command_from_setting(self._setting)
        if not runs and not command:
            return f"No notebook answers at {self._url}, and the setting agent_notebook_command is empty."
        if not runs and notebook_answers(self._url):
            # The port has a program that is not the notebook. Squid starts nothing for it and opens nothing.
            return f"A different program answers at {self._url}. Squid does not start the notebook."
        self._start_control_server()
        if runs:
            self._open_page(self._url)
            return None
        os.makedirs(os.path.dirname(os.path.abspath(self._log_path)), exist_ok=True)
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with self._lock:
            if self._closed:
                return "Squid closes: the notebook program was not started."
            try:
                with open(self._log_path, "wb") as log:  # the child keeps its own handle
                    self.process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        **kwargs
                    )
            except OSError as e:
                return f"The notebook program could not start ({command[0]}): {e}"
        deadline = time.monotonic() + self._wait_s
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                return self._failure(f"The notebook program ended with code {code}.")
            if notebook_runs(self._url):
                self._open_page(self._url)
                return None
            time.sleep(0.2)
        self._end_process()
        return self._failure(
            f"The notebook page did not answer at {self._url} in {self._wait_s:g} s; the program was ended."
        )

    def _failure(self, text: str) -> str:
        tail = read_log_tail(self._log_path)
        return (
            f"{text}\n\nIts last output ({self._log_path}):\n{tail}"
            if tail
            else f"{text} Its output is in {self._log_path}."
        )

    def close(self) -> None:
        """Squid closes: end the program if this Squid started it, and start none after this. A notebook that
        someone else started is left alone."""
        with self._lock:
            self._closed = True
        self._end_process()

    def _end_process(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(5.0)
        except subprocess.TimeoutExpired:
            process.kill()
