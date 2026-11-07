import os
import sys
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from app import app
from services import youtube_session as session


CONNECTOR_ORIGIN = "chrome-extension://gkpolooknidedbhcljjpembcjnollpni"
ENDPOINT = "/api/youtube/session/import-extension"
MANAGED_ENDPOINT = "/api/youtube/session/import-managed-browser"


def test_chrome_connector_preflight_is_scoped_to_first_party_extension():
    client = app.test_client()

    response = client.options(ENDPOINT, headers={"Origin": CONNECTOR_ORIGIN})
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == CONNECTOR_ORIGIN

    rejected = client.options(
        ENDPOINT,
        headers={"Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )
    assert rejected.status_code == 403


def test_chrome_connector_import_uses_live_verified_session_broker():
    client = app.test_client()
    records = [{
        "name": "SAPISID",
        "value": "redacted-test-value",
        "domain": ".youtube.com",
        "path": "/",
        "secure": True,
        "expirationDate": 4102444800,
    }]
    with mock.patch.object(session, "import_cookie_records", return_value=(None, 4)) as importer, mock.patch.object(
        session,
        "session_status",
        return_value={"ready": True, "verified_authenticated": True, "source": "Trusted Chrome browser connector"},
    ):
        response = client.post(
            ENDPOINT,
            json={"cookies": records},
            headers={"Origin": CONNECTOR_ORIGIN, "X-Vidzeo-Chrome-Connector": "1"},
        )

    assert response.status_code == 200
    assert response.get_json()["connector"] == "chrome-extension"
    importer.assert_called_once_with(
        records,
        browser="chrome",
        source="Trusted Chrome browser connector",
        profile="Vidzeo YouTube profile",
    )


def test_managed_desktop_connector_requires_local_process_token():
    client = app.test_client()
    records = [{
        "name": "SAPISID",
        "value": "redacted-test-value",
        "domain": ".youtube.com",
        "path": "/",
        "secure": True,
        "expires": -1,
    }]
    with mock.patch.dict(os.environ, {"VIDZEO_DESKTOP_CONNECTOR_TOKEN": "test-token"}, clear=False):
        rejected = client.post(MANAGED_ENDPOINT, json={"browser": "chrome", "cookies": records})
        assert rejected.status_code == 403

        with mock.patch.object(session, "import_cookie_records", return_value=(None, 5)) as importer, mock.patch.object(
            session,
            "session_status",
            return_value={"ready": True, "verified_authenticated": True, "source": "managed"},
        ):
            accepted = client.post(
                MANAGED_ENDPOINT,
                json={"browser": "edge", "cookies": records},
                headers={"X-Vidzeo-Desktop-Connector": "test-token"},
            )

    assert accepted.status_code == 200
    assert accepted.get_json()["connector"] == "managed-browser"
    importer.assert_called_once_with(
        records,
        browser="edge",
        source="Vidzeo managed Edge connection",
        profile="Vidzeo YouTube profile",
    )
