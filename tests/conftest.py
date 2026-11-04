"""Offline tests never read a developer's `.env`. The app builds some settings objects itself (`Settings()` in create_app, a route's default `RevokeTaskSettings()`), which a test
cannot pass `_env_file=None` to; this fixture turns the env file off on every settings class for the offline modules. Live modules (and the role matrix, which runs against the
real database in CI) keep the normal behaviour: environment variables still apply to all of them."""

import importlib

import pytest
from pydantic_settings import BaseSettings

# import the modules that define settings classes, so the subclass walk below finds them all
for _module in ("careflow.app", "careflow.mpi", "careflow.patient_view", "careflow.scheduling_agent"):
    importlib.import_module(_module)


def _settings_classes(base=BaseSettings):
    for sub in base.__subclasses__():
        yield sub
        yield from _settings_classes(sub)


@pytest.fixture(autouse=True)
def _no_env_file_offline(request, monkeypatch):
    name = request.module.__name__
    if "live" in name or name.endswith("test_api_role_matrix"):
        return
    for cls in _settings_classes():
        monkeypatch.setitem(cls.model_config, "env_file", None)
