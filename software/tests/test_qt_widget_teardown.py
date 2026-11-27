"""A test's Qt widgets are destroyed in its own teardown (tests/conftest.py, destroy_closed_qt_widgets).

pytest-qt closes each widget registered with qtbot.addWidget and calls deleteLater() on it, but processEvents()
outside a running event loop never runs that delete. The widget then lived on until a later test ran an event loop,
and was destroyed inside that test.

The second test checks what the first one left. pytest runs a file's tests in order in one process, and CI's shards
split the suite by whole file.
"""

import weakref

import pytest
from qtpy.QtWidgets import QWidget

_left_by_the_first_test = {}


def test_a_test_leaves_its_widgets_to_pytest_qt(qtbot):
    held = QWidget()  # a reference no collector frees: only the DeferredDelete pytest-qt posts can destroy it
    qtbot.addWidget(held)
    in_a_cycle = QWidget()
    in_a_cycle.self_reference = in_a_cycle  # a Python wrapper only the cycle collector frees
    qtbot.addWidget(in_a_cycle)
    _left_by_the_first_test["held"] = held
    _left_by_the_first_test["in_a_cycle"] = weakref.ref(in_a_cycle)


def test_they_are_destroyed_by_the_end_of_its_teardown():
    if not _left_by_the_first_test:
        pytest.skip("checks what the test above left, so it runs after it in the same process")
    with pytest.raises(RuntimeError, match="has been deleted"):
        _left_by_the_first_test["held"].objectName()
    assert _left_by_the_first_test["in_a_cycle"]() is None
