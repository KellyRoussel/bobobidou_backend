import os

# Settings are read at import: test values, no real database is reached by these tests
os.environ.setdefault("SECRET_KEY", "test-secret-of-at-least-thirty-two-bytes")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("AUTH_REDIRECT_URI", "http://testserver/auth/callback")

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app)
