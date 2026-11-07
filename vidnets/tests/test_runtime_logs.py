import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app


def test_runtime_log_history_endpoint_is_available():
    client = server_app.app.test_client()
    response = client.get("/api/runtime/logs?limit=3")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload.get("lines"), list)
    assert len(payload["lines"]) <= 3
    assert payload["count"] == len(payload["lines"])


def test_favicon_is_served_from_packaged_public_assets():
    client = server_app.app.test_client()
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.mimetype == "image/x-icon"
