"""Shared fixtures for Kronk tests."""
import os
import sys
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Services whose source can be added to sys.path for import.
# Order matters: later entries are appended, not prepended.
_SERVICE_DIRS = [
    "orchestrator",
    "finance_service",
    # health_service is NOT added here — it shares the bare module name 'db'
    # with finance_service. Use the use_health_service fixture instead.
]

for d in _SERVICE_DIRS:
    path = os.path.join(REPO_ROOT, d)
    if path not in sys.path:
        sys.path.append(path)


@pytest.fixture
def use_health_service():
    """
    Temporarily make health_service importable as the primary 'db' module.

    Removes finance_service from sys.path and clears any cached 'db' module
    so health_service/db.py wins the bare 'import db' resolution.
    Restores sys.path and sys.modules on teardown.
    """
    health_path = os.path.join(REPO_ROOT, "health_service")
    finance_path = os.path.join(REPO_ROOT, "finance_service")

    original_path = list(sys.path)
    cached_db = sys.modules.pop("db", None)
    cached_health_main = sys.modules.pop("health_service.main", None)

    # Put health_service at the FRONT, remove finance_service temporarily
    new_path = [health_path] + [p for p in sys.path if p != finance_path]
    sys.path[:] = new_path

    yield

    # Restore everything
    sys.path[:] = original_path
    sys.modules.pop("db", None)
    if cached_db is not None:
        sys.modules["db"] = cached_db
    if cached_health_main is not None:
        sys.modules["health_service.main"] = cached_health_main
