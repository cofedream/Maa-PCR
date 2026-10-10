"""Label optional private replays so public CI cannot count them as passing tests."""
import ast
import inspect
import textwrap

import pytest


def _private_skip(source):
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'pytest'
                and node.func.attr in ('skip', 'skipif')):
            reasons = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            reasons += [arg.value.value for arg in node.keywords if arg.arg == 'reason'
                        and isinstance(arg.value, ast.Constant) and isinstance(arg.value.value, str)]
            if any('private' in reason.casefold() or 'not distributed' in reason.casefold() for reason in reasons):
                return True
    return False


def pytest_collection_modifyitems(items):
    for item in items:
        try:
            source = inspect.getsource(item.obj)
        except (OSError, TypeError):
            continue
        module_private = any('private' in str(mark.kwargs.get('reason', '')).casefold()
                             or 'not distributed' in str(mark.kwargs.get('reason', '')).casefold()
                             for mark in item.iter_markers('skipif'))
        if module_private or _private_skip(source):
            item.add_marker(pytest.mark.private_replay)
