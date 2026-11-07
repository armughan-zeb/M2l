from __future__ import annotations

import os

for key, val in list(os.environ.items()):
    if len(val) > 32000:
        os.environ.pop(key, None)

import pytest


@pytest.fixture(autouse=True)
def isolated_render_lock(tmp_path, monkeypatch):
    # Offline tests must not wait on or acquire the operator's live render slot.
    # Individual lock-contract tests can still replace this environment value.
    monkeypatch.setenv('VIDNETS_RENDER_LOCK_FILE', str(tmp_path / 'render-test.lock'))


class _OfflineDirectorProvider:
    """Make default director tests deterministic and prevent hidden live API calls."""

    model = "test/offline-director"
    last_model = model
    model_chain = (model,)
    attempt_history = ()

    def chat(self, **_kwargs):
        raise RuntimeError("offline test provider")


@pytest.fixture
def offline_director():
    from server.services.editorial_director import EditingDirector

    return EditingDirector(llm_client=_OfflineDirectorProvider())
