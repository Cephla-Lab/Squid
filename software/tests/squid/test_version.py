# tests/squid/test_version.py
import subprocess
from types import SimpleNamespace

import pytest

import squid.version


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(squid.version, "_cached_version", None)


def test_software_version_is_a_non_empty_string_and_cached(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="v1.2.3-4-gabcdef\n", stderr="")

    monkeypatch.setattr(squid.version.subprocess, "run", fake_run)

    first = squid.version.software_version()
    second = squid.version.software_version()

    assert first == second == "v1.2.3-4-gabcdef"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ["git", "describe", "--tags", "--always", "--dirty"]
    assert kwargs["timeout"] == 2


def test_software_version_unknown_when_git_fails(monkeypatch):
    def fake_run(args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(squid.version.subprocess, "run", fake_run)

    assert squid.version.software_version() == "unknown"


def test_software_version_unknown_when_git_returns_an_error(monkeypatch):
    monkeypatch.setattr(
        squid.version.subprocess,
        "run",
        lambda args, **kwargs: SimpleNamespace(returncode=128, stdout="", stderr="not a git repository"),
    )

    assert squid.version.software_version() == "unknown"


def test_software_version_unknown_when_git_times_out(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))

    monkeypatch.setattr(squid.version.subprocess, "run", fake_run)

    assert squid.version.software_version() == "unknown"
