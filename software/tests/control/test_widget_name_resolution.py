"""Static guard: every global name a start path calls must actually exist.

A merge once left two pre-flight blocks in toggle_acquisition -- the new pacing call
and the old one whose function had been renamed away. Starting any acquisition raised
NameError after the experiment folder was already created, while every test suite
stayed green: nothing invokes toggle_acquisition, so a dangling name in the widget's
start path is invisible to the rest of the tests.

This walks the AST instead of executing anything, so it is fast and needs no hardware,
no Qt event loop, and no new dependency.
"""

import ast
import builtins
import os

import pytest

WIDGETS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "control", "widgets.py")

# The methods an operator's click actually runs. A dangling name in any of these takes
# the instrument down, so they are worth pinning even though the check is general.
CRITICAL_METHODS = [
    ("FlexibleMultiPointWidget", "toggle_acquisition"),
    ("WellplateMultiPointWidget", "toggle_acquisition"),
    ("FlexibleMultiPointWidget", "on_snap_images"),
    ("WellplateMultiPointWidget", "on_snap_images"),
    ("_TimingSimulationMixin", "_start_timing_simulation"),
    ("_TimingSimulationMixin", "_on_timing_probe_finished"),
]


def _module_ast():
    with open(WIDGETS_PATH, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename="widgets.py")


def _module_level_names():
    """The real module namespace, which is what NameError is resolved against.

    Taken from the imported module rather than reconstructed from the AST: widgets.py
    uses star imports from qtpy, whose contents cannot be known statically.
    """
    import control.widgets

    return set(vars(control.widgets)) | set(dir(builtins))


def _find_method(tree, class_name, method_name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    return item
    return None


def _locally_bound(func_node):
    """Names bound inside the function: args, assignments, comprehensions, handlers."""
    bound = set()
    args = func_node.args
    for arg in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs):
        bound.add(arg.arg)
    if args.vararg:
        bound.add(args.vararg.arg)
    if args.kwarg:
        bound.add(args.kwarg.arg)
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return bound


@pytest.mark.parametrize("class_name,method_name", CRITICAL_METHODS)
def test_start_path_names_all_resolve(class_name, method_name):
    tree = _module_ast()
    method = _find_method(tree, class_name, method_name)
    assert method is not None, f"{class_name}.{method_name} not found in widgets.py"

    known = _module_level_names() | _locally_bound(method)
    referenced = {node.id for node in ast.walk(method) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}

    unresolved = sorted(referenced - known)
    assert not unresolved, (
        f"{class_name}.{method_name} references name(s) that do not exist at module level: {unresolved}. "
        "This is the shape of bug that takes acquisition down at runtime while the suites stay green."
    )
