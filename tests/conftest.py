import json
from pathlib import Path

import pytest

from repo_history_tracking import report_from_json

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
