"""Shared test classification and the opt-in public fixture interface."""

from pathlib import Path

import pytest

pytest_plugins = ["autoweb.pytest_plugin", "pytester"]


def pytest_collection_modifyitems(items):
    for item in items:
        folder = Path(item.path).parent.name
        if folder in {"unit", "integration", "e2e"}:
            item.add_marker(getattr(pytest.mark, folder))
