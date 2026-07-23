import os

# set QT_API environment variable
os.environ["QT_API"] = "pyqt6"

import qtpy
from qtpy.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QLabel
from qtpy.QtCore import QThread, Signal, Qt, QObject, QMetaObject
import sys
import code

if sys.platform == "win32":
    from pyreadline3 import Readline

    readline = Readline()
else:
    import readline
import rlcompleter
import threading
import traceback
import functools
import inspect
from qtconsole.rich_jupyter_widget import RichJupyterWidget
from qtconsole.inprocess import QtInProcessKernelManager


class QtCompleter:
    """Custom completer for Qt objects"""

    def __init__(self, namespace):
        self.namespace = namespace

    def complete(self, text, state):
        if state == 0:
            if "." in text:
                self.matches = self.attr_matches(text)
            else:
                # Complete global namespace items
                self.matches = self.global_matches(text)
        try:
            return self.matches[state]
        except IndexError:
            return None

    def global_matches(self, text):
        """Compute matches when text is a simple name."""
        matches = []
        n = len(text)
        for word in self.namespace:
            if word[:n] == text:
                matches.append(word)
        return matches

    def attr_matches(self, text):
        """Match attributes of an object."""
        # Split the text on dots
        parts = text.split(".")
        if not parts:
            return []

        # Find the object we're looking for completions on
        try:
            obj = self.namespace[parts[0]]
            for part in parts[1:-1]:
                if isinstance(obj, GuiProxy):
                    obj = obj.target
                obj = getattr(obj, part)

            if isinstance(obj, GuiProxy):
                obj = obj.target
        except (KeyError, AttributeError):
            return []

        # Get the incomplete name we're trying to match
        incomplete = parts[-1]

        # Get all possible matches
        matches = []

        try:
            # Get standard Python attributes
            matches.extend(name for name in dir(obj) if name.startswith(incomplete))

            # If it's a QObject, also get Qt properties
            if isinstance(obj, QObject):
                meta = obj.metaObject()
                for i in range(meta.propertyCount()):
                    prop = meta.property(i)
                    name = prop.name()
                    if name.startswith(incomplete):
                        matches.append(name)

            # Get methods with their signatures
            if incomplete:
                matches.extend(
                    f"{name}()"
                    for name, member in inspect.getmembers(obj, inspect.ismethod)
                    if name.startswith(incomplete)
                )

        except Exception as e:
            print(f"Error during completion: {e}")
            return []

        # Make the matches into complete dots
        if len(parts) > 1:
            matches = [".".join(parts[:-1] + [m]) for m in matches]

        return matches


class MainThreadCall(QObject):
    """Helper class to execute functions on the main thread"""

    execute_signal = Signal(object, tuple, dict)

    def __init__(self):
        super().__init__()
        self.moveToThread(QApplication.instance().thread())
        self.execute_signal.connect(self._execute)
        self._result = None
        self._event = threading.Event()

    def _execute(self, func, args, kwargs):
        try:
            self._result = func(*args, **kwargs)
        except Exception as e:
            self._result = e
        finally:
            self._event.set()

    def __call__(self, func, *args, **kwargs):
        if QThread.currentThread() is QApplication.instance().thread():
            return func(*args, **kwargs)

        self._event.clear()
        self._result = None
        self.execute_signal.emit(func, args, kwargs)
        self._event.wait()

        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class GuiProxy:
    """Proxy class to safely execute GUI operations from other threads"""

    def __init__(self, target_object):
        self.target = target_object
        self.main_thread_call = MainThreadCall()

    def __getattr__(self, name):
        attr = getattr(self.target, name)
        if callable(attr):

            @functools.wraps(attr)
            def wrapper(*args, **kwargs):
                return self.main_thread_call(attr, *args, **kwargs)

            return wrapper
        return attr

    def __dir__(self):
        """Support for auto-completion"""
        return dir(self.target)


class EnhancedInteractiveConsole(code.InteractiveConsole):
    """Enhanced console with better completion support"""

    def __init__(self, locals=None):
        super().__init__(locals)
        # Set up readline with our custom completer
        self.completer = QtCompleter(locals)
        readline.set_completer(self.completer.complete)
        readline.parse_and_bind("tab: complete")

        # Use better completion delimiters
        readline.set_completer_delims(" \t\n`~!@#$%^&*()-=+[{]}\\|;:'\",<>?")

        # Set up readline history
        import os

        histfile = os.path.expanduser("~/.pyqt_console_history")
        try:
            readline.read_history_file(histfile)
        except FileNotFoundError:
            pass
        import atexit

        atexit.register(readline.write_history_file, histfile)


class ConsoleThread(QThread):
    """Thread for running the interactive console"""

    def __init__(self, locals_dict):
        super().__init__()
        self.locals_dict = locals_dict
        self.wrapped_locals = {
            key: GuiProxy(value) if isinstance(value, QObject) else value for key, value in locals_dict.items()
        }
        self.console = EnhancedInteractiveConsole(locals=self.wrapped_locals)

    def run(self):
        while True:
            try:
                self.console.interact(
                    banner="""
Squid Microscope Console
-----------------------
Use 'microscope' to access the microscope
"""
                )
            except SystemExit:
                break


from IPython.core.completer import IPCompleter


class NoFileCompleter(IPCompleter):
    """Custom completer that filters out file completions"""

    def file_matches(self, text):
        """Override file_matches to return empty list"""
        return []


class JupyterWidget(QWidget):
    """Widget that embeds a Jupyter console with PyQt5 integration"""

    kernel_ready = Signal()  # Signal emitted when kernel is ready

    def __init__(self, namespace=None, parent=None):
        super().__init__(parent)

        if namespace is None:
            namespace = {}

        # Create kernel manager and kernel
        self.kernel_manager = QtInProcessKernelManager()
        self.kernel_manager.start_kernel()

        # Get the kernel
        kernel = self.kernel_manager.kernel
        kernel.gui = "qt"

        # Replace the default completer with our custom one
        kernel.shell.Completer = NoFileCompleter(
            shell=kernel.shell,
            namespace=kernel.shell.user_ns,
            global_namespace=kernel.shell.user_global_ns,
            use_jedi=True,
        )

        # Update namespace
        kernel.shell.user_ns.update(namespace)

        # Create kernel client
        self.kernel_client = self.kernel_manager.client()
        self.kernel_client.start_channels()

        # Create Jupyter widget
        self.jupyter_widget = RichJupyterWidget()
        self.jupyter_widget.kernel_manager = self.kernel_manager
        self.jupyter_widget.kernel_client = self.kernel_client

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.jupyter_widget)
        self.setLayout(layout)

        # Emit signal when kernel is ready
        self.kernel_ready.emit()

    def execute_command(self, command):
        """Execute a command in the Jupyter kernel"""
        self.jupyter_widget.execute(command)

    def clear_console(self):
        """Clear the Jupyter console"""
        self.jupyter_widget.clear()

    def closeEvent(self, event):
        """Handle cleanup when widget is closed"""
        self.kernel_client.stop_channels()
        self.kernel_manager.shutdown_kernel()
        event.accept()
