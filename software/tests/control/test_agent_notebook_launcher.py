"""Utils > Agent Notebook: Squid starts the lab notebook of an agent session and opens its page.

The notebook is a separate program. Squid does not import it: it has a command line and a page address from two
settings. With no command (the default) there is no menu entry."""

import http.server
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime

import pytest

from control.agent_notebook_launcher import (
    SERVICE_ANSWER_MAX_BYTES,
    AgentNotebook,
    command_from_setting,
    notebook_answers,
    notebook_runs,
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# A notebook program says what it is at api/service; each other path is its page.
WHAT_IT_IS = b'{"role": "agent-notebook", "kind": "home", "version": "0.0.1"}'
PAGE_PROGRAM = (
    "import http.server, sys\n"
    "class Page(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        is_service = self.path == '/api/service'\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Type', 'application/json' if is_service else 'text/html')\n"
    "        self.end_headers()\n"
    f"        self.wfile.write({WHAT_IT_IS!r} if is_service else b'notebook')\n"
    "    def log_message(self, *args): pass\n"
    "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Page).serve_forever()\n"
)


def serve(answer_for):
    """A program that answers on a free port: answer_for(path) gives the body. Returns (the address, the server)."""
    port = free_port()

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(answer_for(self.path))

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/", server


@pytest.fixture
def page_that_runs():
    """A notebook that answers already (someone started it before)."""
    url, server = serve(lambda path: WHAT_IT_IS if path == "/api/service" else b"notebook")
    yield url
    server.shutdown()


@pytest.fixture
def a_different_program():
    """A program that is not the notebook, at the address of the notebook: it answers the same to each path."""
    url, server = serve(lambda path: b"notebook")
    yield url
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


def test_on_windows_the_command_line_goes_to_windows_as_it_is():
    # Windows reads the command line itself (CreateProcess): its backslashes and quotes are not those of POSIX.
    setting = r'"C:\Program Files\Python\python.exe" -m my_notebook serve --run-dir C:\runs\run-{timestamp}'
    command = command_from_setting(setting, datetime(2026, 11, 13, 1, 9, 30), windows=True)
    assert command == r'"C:\Program Files\Python\python.exe" -m my_notebook serve --run-dir C:\runs\run-20261113-010930'
    assert (
        command_from_setting(r"C:\Tools\python.exe -m my_notebook", windows=True)
        == r"C:\Tools\python.exe -m my_notebook"
    )
    assert command_from_setting("   ", windows=True) == ""


def test_a_notebook_that_says_what_it_is_is_seen_and_a_closed_port_is_not(page_that_runs, a_different_program):
    assert notebook_runs(page_that_runs) is True and notebook_answers(page_that_runs) is True
    closed = f"http://127.0.0.1:{free_port()}/"
    assert notebook_runs(closed) is False and notebook_answers(closed) is False
    # Something answers there, and it does not say that it is the notebook.
    assert notebook_runs(a_different_program) is False and notebook_answers(a_different_program) is True


def test_a_program_whose_answer_does_not_end_is_not_the_notebook_and_the_question_ends():
    # A different program at the address streams at api/service: its answer never ends.
    port = free_port()

    class Stream(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Stream)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    said = []
    question = threading.Thread(target=lambda: said.append(notebook_runs(f"http://127.0.0.1:{port}/")), daemon=True)
    try:
        question.start()
        question.join(5.0)
        assert not question.is_alive()  # the question has a time limit for the whole answer
        assert said == [False]
    finally:
        server.shutdown()


def test_an_answer_longer_than_the_limit_is_not_the_notebook():
    url, server = serve(lambda path: WHAT_IT_IS + b" " * SERVICE_ANSWER_MAX_BYTES)  # still valid JSON
    try:
        assert notebook_runs(url) is False
    finally:
        server.shutdown()


def test_a_notebook_that_runs_already_gets_the_control_server_and_is_opened(page_that_runs, tmp_path):
    notebook, events = make(f"{sys.executable} -c pass", page_that_runs, tmp_path)
    assert notebook.open() is None
    # The notebook can run with the control server of Squid off (it was started by hand): the entry turns it on.
    assert events == ["control server", f"open {page_that_runs}"]
    assert notebook.process is None  # no second program


def test_a_different_program_at_the_address_is_not_taken_as_the_notebook(a_different_program, tmp_path):
    notebook, events = make(f"{sys.executable} -c pass", a_different_program, tmp_path)
    reason = notebook.open()
    assert reason == f"A different program answers at {a_different_program}. Squid does not start the notebook."
    assert events == [] and notebook.process is None  # no control server, no program, no page


def test_squid_starts_the_control_server_and_the_program_then_opens_the_page(tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    (tmp_path / "page.py").write_text(PAGE_PROGRAM)
    notebook, events = make(f'"{sys.executable}" "{tmp_path / "page.py"}" {port}', url, tmp_path)
    try:
        assert notebook.open() is None
        assert events == ["control server", f"open {url}"]  # the agent of the notebook needs the control server
        assert notebook_answers(url)
        assert notebook.open() is None  # a second click: the control server again (it can be off), and the page
        assert events == ["control server", f"open {url}", "control server", f"open {url}"]
        assert notebook_runs(url)
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


@pytest.mark.skipif(sys.platform == "win32", reason="a process group of POSIX")
def test_close_ends_the_programs_that_the_notebook_program_started(tmp_path):
    # The command can be a wrapper (a script, an environment runner) that starts the notebook as its own child.
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    pid_file = tmp_path / "child.pid"
    (tmp_path / "page.py").write_text(PAGE_PROGRAM)
    (tmp_path / "wrapper.py").write_text(
        "import subprocess, sys\n"
        f"child = subprocess.Popen([sys.executable, {str(tmp_path / 'page.py')!r}, sys.argv[1]])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "child.wait()\n"
    )
    notebook, events = make(f'"{sys.executable}" "{tmp_path / "wrapper.py"}" {port}', url, tmp_path)
    child_pid = None
    try:
        assert notebook.open() is None
        child_pid = int(pid_file.read_text())
        notebook.close()
        assert notebook.process.poll() is not None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and notebook_answers(url):
            time.sleep(0.1)
        assert not notebook_answers(url)  # the notebook itself ended too
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_a_second_click_while_the_program_of_squid_does_not_answer_starts_no_second_program(tmp_path):
    # The program answers once, then it is busy: each later request waits.
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    busy_after_one = PAGE_PROGRAM.replace(
        "    def do_GET(self):\n",
        "    answered = 0\n"
        "    def do_GET(self):\n"
        "        if Page.answered:\n"
        "            import time; time.sleep(60)\n"
        "        Page.answered += 1\n",
    )
    (tmp_path / "page.py").write_text(busy_after_one)
    notebook, events = make(f'"{sys.executable}" "{tmp_path / "page.py"}" {port}', url, tmp_path)
    first = None
    try:
        assert notebook.open() is None
        first = notebook.process
        reason = notebook.open()
        assert notebook.process is first and first.poll() is None  # Squid still owns the program that it started
        assert "does not answer" in reason
        notebook.close()
        assert first.poll() is not None  # and ends it at the close
    finally:
        if first is not None and first.poll() is None:
            first.kill()


def test_squid_that_closes_during_the_start_starts_no_program(tmp_path):
    # The quit of Squid comes while the start runs on its thread, before the program is started.
    url = f"http://127.0.0.1:{free_port()}/"
    (tmp_path / "idle.py").write_text("import time; time.sleep(60)")
    notebook = AgentNotebook(
        f'"{sys.executable}" "{tmp_path / "idle.py"}"',
        url,
        start_control_server=lambda: notebook.close(),
        open_page=lambda address: None,
        log_path=str(tmp_path / "agent_notebook.log"),
        wait_s=0.6,
    )
    try:
        reason = notebook.open()
        assert notebook.process is None  # nothing that goes on after Squid
        assert "closes" in reason
    finally:
        if notebook.process is not None:
            notebook.process.kill()


def test_a_setting_that_can_not_be_read_is_a_reason_that_names_the_setting(tmp_path):
    notebook, events = make(
        'python3 -m my_notebook --run-dir "/data/runs', f"http://127.0.0.1:{free_port()}/", tmp_path
    )
    reason = notebook.open()
    assert "agent_notebook_command" in reason and "quotation" in reason
    assert events == [] and notebook.process is None


def test_with_no_command_and_no_page_the_reason_names_the_setting(tmp_path):
    notebook, events = make("", f"http://127.0.0.1:{free_port()}/", tmp_path)
    assert "agent_notebook_command" in notebook.open() and events == []
