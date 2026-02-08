"""Tests for workflow-tree start-position selection behavior."""

import pytest

from squid.core.events import EventBus
from squid.ui.widgets.orchestrator.orchestrator_widget import OrchestratorWorkflowTree


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()


@pytest.fixture
def workflow_tree(event_bus, qtbot):
    tree = OrchestratorWorkflowTree(event_bus=event_bus)
    qtbot.addWidget(tree)
    return tree


def test_clicking_operation_sets_nonzero_start_step(workflow_tree, qtbot):
    protocol = {
        "name": "Two-Step Protocol",
        "rounds": [
            {
                "name": "Round 1",
                "steps": [
                    {"step_type": "fluidics", "protocol": "wash"},
                    {"step_type": "imaging", "protocol": "standard"},
                ],
            },
        ],
    }
    workflow_tree.populate_from_protocol(protocol)

    emitted = []
    workflow_tree.start_position_changed.connect(
        lambda round_idx, step_idx, fov_idx: emitted.append((round_idx, step_idx, fov_idx))
    )

    op_item = workflow_tree._tree_items[(0, 1)]
    workflow_tree._on_item_clicked(op_item, 0)
    qtbot.wait(10)

    assert emitted
    assert emitted[-1] == (0, 1, 0)


def test_clicking_fov_sets_start_fov_index(workflow_tree, qtbot):
    protocol = {
        "name": "Imaging Protocol",
        "rounds": [
            {
                "name": "Round 1",
                "steps": [
                    {"step_type": "fluidics", "protocol": "wash"},
                    {"step_type": "imaging", "protocol": "standard"},
                ],
            },
        ],
    }
    workflow_tree.set_fov_positions(
        {
            "region_1": [(1.0, 2.0, 0.0), (3.0, 4.0, 0.0)],
        }
    )
    workflow_tree.populate_from_protocol(protocol)

    emitted = []
    workflow_tree.start_position_changed.connect(
        lambda round_idx, step_idx, fov_idx: emitted.append((round_idx, step_idx, fov_idx))
    )

    fov_item = workflow_tree._tree_items[(0, 1, 1)]
    workflow_tree._on_item_clicked(fov_item, 0)
    qtbot.wait(10)

    assert emitted
    assert emitted[-1] == (0, 1, 1)


def test_double_click_fov_when_idle_starts_from_fov(workflow_tree, qtbot):
    protocol = {
        "name": "Imaging Protocol",
        "rounds": [
            {
                "name": "Round 1",
                "steps": [
                    {"step_type": "imaging", "protocol": "standard"},
                ],
            },
        ],
    }
    workflow_tree.set_fov_positions(
        {
            "region_1": [(1.0, 2.0, 0.0), (3.0, 4.0, 0.0)],
        }
    )
    workflow_tree.populate_from_protocol(protocol)

    requested = []
    workflow_tree.start_from_requested.connect(
        lambda round_idx, step_idx, fov_idx: requested.append((round_idx, step_idx, fov_idx))
    )

    fov_item = workflow_tree._tree_items[(0, 0, 1)]
    workflow_tree._on_item_double_clicked(fov_item, 0)
    qtbot.wait(10)

    assert requested
    assert requested[-1] == (0, 0, 1)


def test_fov_status_updates_are_scoped_by_round(workflow_tree):
    protocol = {
        "name": "Two-Round Imaging Protocol",
        "rounds": [
            {
                "name": "Round 1",
                "steps": [
                    {"step_type": "imaging", "protocol": "standard"},
                ],
            },
            {
                "name": "Round 2",
                "steps": [
                    {"step_type": "imaging", "protocol": "standard"},
                ],
            },
        ],
    }
    workflow_tree.set_fov_positions(
        {
            "Region1": [(0.0, 0.0, 0.0), (0.5, 0.0, 0.0)],
        }
    )
    workflow_tree.populate_from_protocol(protocol)

    round1_fov1 = workflow_tree._tree_items[(0, 0, 0)]
    round2_fov1 = workflow_tree._tree_items[(1, 0, 0)]

    workflow_tree._handle_fov_started_ui("Region1_0000", 0, 0, 0, 0.0, 0.0)
    assert round1_fov1.text(1) == "running"
    assert round2_fov1.text(1) == "pending"

    workflow_tree._handle_fov_completed_ui("Region1_0000", 0, 0, "COMPLETED", "")
    assert round1_fov1.text(1) == "completed"
    assert round2_fov1.text(1) == "pending"

    workflow_tree._handle_fov_started_ui("Region1_0000", 0, 1, 0, 0.0, 0.0)
    assert round2_fov1.text(1) == "running"
