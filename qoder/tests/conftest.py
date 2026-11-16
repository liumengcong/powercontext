"""Shared pytest fixtures: a real PowerContext builtin runtime on a throwaway SQLite file."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_TMP = PROJECT_ROOT / ".pytest-tmp"


@pytest.fixture(scope="session", autouse=True)
def _clean_test_tmp() -> None:
    yield
    if TEST_TMP.exists() and os.environ.get("STUDYFLOW_KEEP_TMP") != "1":
        shutil.rmtree(TEST_TMP, ignore_errors=True)


def make_settings(name: str):
    from backend.config import build_settings

    data_dir = TEST_TMP / name
    if data_dir.exists():
        shutil.rmtree(data_dir)
    os.environ["STUDYFLOW_DATA_DIR"] = str(data_dir)
    os.environ["STUDYFLOW_DB_PATH"] = str(data_dir / "powercontext.db")
    os.environ["STUDYFLOW_SAMPLE_DATA"] = str(PROJECT_ROOT / "sample-data.json")
    os.environ["STUDYFLOW_WORKSPACE"] = "测试工作区"
    return build_settings()


@pytest.fixture()
def settings():
    return make_settings("app")


@pytest.fixture()
def store_app(settings):
    from backend.app import create_app

    return create_app(settings)


@pytest.fixture()
def client(store_app):
    from fastapi.testclient import TestClient

    with TestClient(store_app) as test_client:
        yield test_client


@pytest.fixture()
async def live_store():
    """A real powercontext[builtin] runtime, no mocks anywhere in this fixture."""
    from backend.store import PowerContextStore

    database = TEST_TMP / "store"
    if database.exists():
        shutil.rmtree(database)
    database.mkdir(parents=True)
    instance = PowerContextStore(
        database_url=f"sqlite+aiosqlite:///{database / 'powercontext.db'}",
        scheduler_path=database / "powercontext.scheduler.db",
    )
    await instance.start()
    yield instance
    await instance.stop()
