"""Exercise distributed resources outside the source checkout, without network access."""

import json
import subprocess
import tarfile
import venv
import zipfile
from pathlib import Path

from build import ProjectBuilder

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "repo_history_tracking/schemas/monthly-report-v1.json"


def test_distributions_and_isolated_wheel_install(tmp_path):
    output = tmp_path / "dist"
    builder = ProjectBuilder(str(ROOT))
    sdist = Path(builder.build("sdist", str(output)))
    with tarfile.open(sdist) as archive:
        assert any(name.endswith("src/" + SCHEMA) for name in archive.getnames())
        assert any(
            name.endswith("tests/test_queries.py") for name in archive.getnames()
        )
        assert any(
            name.endswith("tests/fixtures/mixed-2024-02.json")
            for name in archive.getnames()
        )
        archive.extractall(tmp_path / "unpacked", filter="data")
    # Build the wheel from the sdist to catch source distribution omissions.
    source = next((tmp_path / "unpacked").iterdir())
    wheel = Path(ProjectBuilder(str(source)).build("wheel", str(output)))
    with zipfile.ZipFile(wheel) as archive:
        assert SCHEMA in archive.namelist()
        schema = json.loads(archive.read(SCHEMA))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    # Share only runtime dependencies; install our package into the fresh venv.
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / "bin" / "python"
    subprocess.run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--ignore-installed",
            str(wheel),
        ],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    fixtures = [
        (ROOT / "tests" / "fixtures" / name).read_text()
        for name in ("empty-2024-02.json", "mixed-2024-02.json")
    ]
    script = """
import json
import sys
from pathlib import Path
import repo_history_tracking as r
assert Path(r.__file__).is_relative_to(Path(sys.prefix)), r.__file__
assert r.load_schema()['$id'] == 'urn:repo-history-tracking:monthly-report:1'
from repo_history_tracking.queries import REPOSITORY_QUERY, PAGE_INFO_FRAGMENT
assert 'query RepositoryLookup' in REPOSITORY_QUERY
assert 'endCursor' in PAGE_INFO_FRAGMENT
for name in ('GraphQLClient', 'resolve_token', 'RepositorySnapshot',
             'lookup_repository', 'GitHubAPIError', 'AuthenticationError',
             'TransportError', 'GraphQLError', 'RateLimitError',
             'PaginationError', 'RepositoryUnavailableError', 'QueryResourceError'):
    assert getattr(r, name) is not None
for text in json.load(sys.stdin):
    report = r.report_from_json(text)
    assert r.report_from_json(r.report_to_json(report)) == report
"""
    result = subprocess.run(
        [str(python), "-I", "-c", script],
        check=True,
        cwd=tmp_path,
        input=json.dumps(fixtures),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
