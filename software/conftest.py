import pyqtgraph

# pyqtgraph's atexit cleanup sweeps every gc-tracked object to re-parent stray
# QGraphicsItems while the Qt C++ side is already tearing down. In CI this sweep
# intermittently segfaults the interpreter AFTER the whole suite has passed
# (exit code 139 with "1488 passed" above it). The sweep only matters for
# long-lived interactive sessions; a test process is about to exit anyway.
pyqtgraph.setConfigOptions(exitCleanup=False)
