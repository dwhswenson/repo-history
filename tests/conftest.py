import json
from pathlib import Path

import pytest
import requests

import repo_history as api
from repo_history import report_from_json

from .helpers import Session

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mixed_data():
    return json.loads((FIXTURES / "mixed-2024-02.json").read_text())


@pytest.fixture
def mixed():
    return report_from_json((FIXTURES / "mixed-2024-02.json").read_text())


@pytest.fixture
def empty():
    return report_from_json((FIXTURES / "empty-2024-02.json").read_text())


@pytest.fixture
def client(monkeypatch):
    session = Session()
    monkeypatch.setattr(requests, "Session", lambda: session)
    result = api.GraphQLClient(" secret-token ")
    result._sleep = [].append
    result._clock = lambda: 1000
    yield result
    result.close()
