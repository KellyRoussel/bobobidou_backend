import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.e2e.stubs import StubServer

# URL of the running container; without it, these tests are skipped (plain `pytest`)
BASE_URL = os.getenv("E2E_BASE_URL")


def container_env() -> dict[str, str]:
    lines = (Path(__file__).parent / "container.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


def pytest_collection_modifyitems(config, items):
    if BASE_URL:
        return
    skip = pytest.mark.skip(reason="E2E_BASE_URL not set (end-to-end tests of the container)")
    for item in items:
        if item.path.is_relative_to(Path(__file__).parent):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def stub():
    port = urlparse(container_env()["GOOGLE_TOKEN_URL"]).port
    server = StubServer(port)
    server.start()
    yield server
    server.shutdown()


@pytest.fixture(scope="session")
def api(stub):
    # trust_env=False: no proxy between the tests and the container
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=30) as client:
        yield client


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def login(api: httpx.Client, code: str) -> dict:
    """The app's sign-in: Google URL (state stored in DB), then code exchange."""
    response = api.get("/login/google/bobobidou")
    assert response.status_code == 200
    state = query_of(response.json()["authorization_url"])["state"]
    response = api.get("/auth/exchange/bobobidou", params={"code": code, "service": "GOOGLE", "state": state})
    assert response.status_code == 200, response.text
    return response.json()
