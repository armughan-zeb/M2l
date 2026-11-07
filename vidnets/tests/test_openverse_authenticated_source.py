import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = dict(headers or {})
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *, posts=(), gets=()):
        self.headers = {}
        self._posts = list(posts)
        self._gets = list(gets)
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if not self._posts:
            raise AssertionError("unexpected Openverse token request")
        response = self._posts.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self._gets:
            raise AssertionError("unexpected Openverse search request")
        response = self._gets.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _work_payload():
    return {
        "results": [
            {
                "id": "work-123",
                "title": "Historic railway station",
                "description": "The original station facade",
                "url": "https://images.example/station.jpg",
                "thumbnail": "https://images.example/station-thumb.jpg",
                "foreign_landing_url": "https://museum.example/works/station",
                "detail_url": "https://api.openverse.org/v1/images/work-123/",
                "creator": "Example Museum",
                "creator_url": "https://museum.example",
                "provider": "wikimedia",
                "source": "smithsonian",
                "license": "by-sa",
                "license_version": "4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "attribution": "Historic railway station by Example Museum, CC BY-SA 4.0",
                "width": 1600,
                "height": 1000,
                "tags": [{"name": "railway"}, {"name": "history"}],
                "mature": False,
            }
        ]
    }


def test_authenticated_search_caches_token_and_retains_public_provenance(monkeypatch):
    from services.sources.openverse_source import OpenverseSource

    token = "private-bearer-token"
    client_id = "private-client-id"
    client_secret = "private-client-secret"
    session = FakeSession(
        posts=[FakeResponse(200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600})],
        gets=[FakeResponse(200, _work_payload()), FakeResponse(200, _work_payload())],
    )
    source = OpenverseSource(
        client_id=client_id,
        client_secret=client_secret,
        config={"client_secret": client_secret, "access_token": "must-not-survive"},
        session=session,
    )
    monkeypatch.setattr(source, "_rate_limit", lambda: None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(lambda _index: source.search("railway station", limit=1), range(2)))

    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 2
    assert all(
        call[1]["headers"] == {"Authorization": f"Bearer {token}"}
        for call in session.get_calls
    )
    candidate = rows[0][0]
    assert candidate.license == "by-sa"
    assert candidate.metadata["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert candidate.metadata["license_version"] == "4.0"
    assert candidate.metadata["creator"] == "Example Museum"
    assert candidate.metadata["creator_url"] == "https://museum.example"
    assert candidate.metadata["attribution"].endswith("CC BY-SA 4.0")
    assert candidate.metadata["foreign_url"] == "https://museum.example/works/station"
    assert candidate.metadata["license_verification_required"] is True

    externally_cacheable = json.dumps(
        {
            "candidate": candidate.to_dict(),
            "source_config": source.config,
            "session_headers": session.headers,
            "repr": repr(source),
        },
        sort_keys=True,
    )
    assert client_id not in externally_cacheable
    assert client_secret not in externally_cacheable
    assert token not in externally_cacheable
    assert "Authorization" not in session.headers


def test_source_page_license_is_independently_verified_without_bearer_leakage(monkeypatch):
    from services.sources.openverse_source import OpenverseSource

    token = "private-bearer-token"
    session = FakeSession(
        posts=[FakeResponse(200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600})],
        gets=[
            FakeResponse(200, _work_payload()),
            FakeResponse(
                200,
                text=(
                    '<a rel="license" '
                    'href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>'
                ),
            ),
        ],
    )
    source = OpenverseSource(
        "client",
        "secret",
        config={"verify_license_pages": True, "license_verification_limit": 1},
        session=session,
    )
    monkeypatch.setattr(source, "_rate_limit", lambda: None)

    candidate = source.search("railway station", limit=1)[0]

    assert candidate.metadata["license_verified"] is True
    assert candidate.metadata["license_verification_required"] is False
    assert candidate.metadata["license_verification_evidence"] == "source_page_license_match"
    assert candidate.metadata["rights_status"] == "source_page_verified"
    assert session.get_calls[0][1]["headers"] == {"Authorization": f"Bearer {token}"}
    assert session.get_calls[1][1]["headers"] == {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
    }
    assert token not in json.dumps(candidate.to_dict(), sort_keys=True)


def test_token_and_search_retry_after_are_honored(monkeypatch):
    from services.sources import openverse_source

    sleeps = []
    monkeypatch.setattr(openverse_source.time, "sleep", lambda seconds: sleeps.append(round(seconds, 3)))
    session = FakeSession(
        posts=[
            FakeResponse(429, headers={"Retry-After": "2"}),
            FakeResponse(200, {"access_token": "token", "token_type": "Bearer", "expires_in": 3600}),
        ],
        gets=[
            FakeResponse(429, headers={"Retry-After": "3"}),
            FakeResponse(200, {"results": []}),
        ],
    )
    source = openverse_source.OpenverseSource("client", "secret", session=session)

    assert source.search("railway station", limit=2) == []
    assert len(session.post_calls) == 2
    assert len(session.get_calls) == 2
    assert 2.0 in sleeps
    assert 3.0 in sleeps


def test_invalid_credentials_do_not_silently_downgrade_to_anonymous():
    from services.sources.openverse_source import OpenverseSource

    session = FakeSession(posts=[FakeResponse(401, {"detail": "invalid_client"})])
    source = OpenverseSource("bad-client", "bad-secret", session=session)

    assert source.search("railway station") == []
    assert source.search("railway station second query") == []
    assert len(session.post_calls) == 1
    assert session.get_calls == []


def test_transient_token_service_failure_can_use_documented_anonymous_mode(monkeypatch):
    from services.sources import openverse_source

    monkeypatch.setattr(openverse_source.time, "sleep", lambda _seconds: None)
    session = FakeSession(
        posts=[FakeResponse(503), FakeResponse(503)],
        gets=[FakeResponse(200, _work_payload()), FakeResponse(200, _work_payload())],
    )
    source = openverse_source.OpenverseSource("client", "secret", session=session)
    monkeypatch.setattr(source, "_rate_limit", lambda: None)

    results = source.search("railway station", limit=1)
    second_results = source.search("railway station exterior", limit=1)

    assert len(results) == 1
    assert len(second_results) == 1
    assert len(session.post_calls) == 2
    assert session.get_calls[0][1]["headers"] == {}
    serialized = json.dumps(results[0].to_dict(), sort_keys=True)
    assert "client" not in serialized
    assert "secret" not in serialized


def test_authenticated_unauthorized_search_refreshes_but_never_downgrades():
    from services.sources.openverse_source import OpenverseSource

    session = FakeSession(
        posts=[
            FakeResponse(200, {"access_token": "token-one", "token_type": "Bearer", "expires_in": 3600}),
            FakeResponse(200, {"access_token": "token-two", "token_type": "Bearer", "expires_in": 3600}),
        ],
        gets=[FakeResponse(401), FakeResponse(403)],
    )
    source = OpenverseSource("client", "secret", session=session)
    source._rate_limit = lambda: None

    assert source.search("railway station") == []
    assert len(session.post_calls) == 2
    assert [call[1]["headers"] for call in session.get_calls] == [
        {"Authorization": "Bearer token-one"},
        {"Authorization": "Bearer token-two"},
    ]
