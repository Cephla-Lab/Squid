"""Recognises a spurious Qt-loop exception seen while the full GUI is built on CI (see conftest's
ignore_stray_qevent_call)."""


def is_stray_qevent_call(exc_type, value, tb) -> bool:
    """True for the TypeError PyQt reports when Qt runs a zero-argument callback whose target is the QEvent class
    itself: no Python frame, and every overload in the message is a QEvent constructor short of arguments."""
    if exc_type is not TypeError or tb is not None:
        return False
    lines = [line.strip() for line in str(value).splitlines()]
    overloads = lines[1:]
    return (
        lines[0] == "arguments did not match any overloaded call:"
        and bool(overloads)
        and all(line.startswith("QEvent(") and line.endswith("): not enough arguments") for line in overloads)
    )
