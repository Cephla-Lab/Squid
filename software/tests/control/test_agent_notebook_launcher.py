"""Utils > Agent Notebook: Squid starts the lab notebook of an agent session and opens its page.

The notebook is a separate program. Squid does not import it: it has a command line and a page address from two
settings. With no command (the default) there is no menu entry."""

import http.server
import socket
import sys
import threading
from datetime import datetime

import pytest

from control.agent_notebook_launcher import (
    AgentNotebook,
    command_from_setting,
    notebook_answers,
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PAGE_PROGRAM = (
    "import http.server, sys\n"
    "class Page(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200); self.end_headers(); self.wfile.write(b'notebook')\n"
    "    def log_message(self, *args): pass\n"
    "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Page).serve_forever()\n"
)


@pytest.fixture
def page_that_runs():
    """A page that answers already (someone started the notebook before)."""
    port = free_port()

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"notebook")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/"
    server.shutdown()


def make(setting, url, tmp_path, wait_s=15.0):
    events = []
    notebook = AgentNotebook(
        setting,
        url,
        start_control_server=lambda: events.append("control server"),
        open_page=lambda address: events.append(f"open {address}"),
        log_path=str(tmp_path / "agent_notebook.log"),
        wait_s=wait_s,
    )
    return notebook, events


def test_the_command_comes_from_the_setting_with_the_time_of_the_start():
    setting = 'python3 -m lab_notebook serve --run-dir "/data/my runs/run-{timestamp}" --request "Session from Squid"'
    command = command_from_setting(setting, datetime(2026, 11, 13, 1, 9, 30))
    assert command == [
        "python3",
        "-m",
        "lab_notebook",
        "serve",
        "--run-dir",
        "/data/my runs/run-20261113-010930",
        "--request",
        "Session from Squid",
    ]
    assert command_from_setting("", datetime(2026, 11, 13)) == []
    assert command_from_setting("   ", datetime(2026, 11, 13)) == []


def test_a_page_that_answers_is_seen_and_a_closed_port_is_not(page_that_runs):
    assert notebook_answers(page_that_runs) is True
    assert notebook_answers(f"http://127.0.0.1:{free_port()}/") is False


def test_a_notebook_that_runs_already_is_only_opened(page_that_runs, tmp_path):
    notebook, events = make(f"{sys.executable} -c pass", page_that_runs, tmp_path)
    assert notebook.open() is None
    assert events == [f"open {page_that_runs}"]  # no control server start, no second program


def test_squid_starts_the_control_server_and_the_program_then_opens_the_page(tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    (tmp_path / "page.py").write_text(PAGE_PROGRAM)
    notebook, events = make(f'"{sys.executable}" "{tmp_path / "page.py"}" {port}', url, tmp_path)
    try:
        assert notebook.open() is None
        assert events == ["control server", f"open {url}"]  # the agent of the notebook needs the control server
        assert notebook_answers(url)
        assert notebook.open() is None  # a second click: the page only
        assert events == ["control server", f"open {url}", f"open {url}"]
    finally:
        notebook.close()
    assert notebook.process.wait(10) is not None and not notebook_answers(url)  # Squid ends what it started


def test_a_program_that_ends_at_once_gives_its_code_and_its_last_lines(tmp_path):
    url = f"http://127.0.0.1:{free_port()}/"
    program = "import sys; print('no module named lab_notebook'); sys.exit(3)"
    (tmp_path / "bad.py").write_text(program)
    notebook, events = make(f'"{sys.executable}" "{tmp_path / "bad.py"}"', url, tmp_path)
    reason = notebook.open()
    assert "ended with code 3" in reason and "no module named lab_notebook" in reason
    assert events == ["control server"]  # the page is not opened


def test_a_program_that_runs_but_does_not_answer_is_ended(tmp_path):
    url = f"http://127.0.0.1:{free_port()}/"
    (tmp_path / "idle.py").write_text("import time; time.sleep(60)")
    notebook, events = make(f'"{sys.executable}" "{tmp_path / "idle.py"}"', url, tmp_path, wait_s=0.6)
    reason = notebook.open()
    assert "did not answer" in reason and events == ["control server"]
    assert notebook.process.wait(10) is not None  # not left behind


def test_a_command_that_can_not_start_is_a_reason_not_an_exception(tmp_path):
    url = f"http://127.0.0.1:{free_port()}/"
    notebook, events = make("no-such-program-for-the-notebook --serve", url, tmp_path)
    reason = notebook.open()
    assert "could not start" in reason and "no-such-program-for-the-notebook" in reason


def test_with_no_command_and_no_page_the_reason_names_the_setting(tmp_path):
    notebook, events = make("", f"http://127.0.0.1:{free_port()}/", tmp_path)
    assert "agent_notebook_command" in notebook.open() and events == []
