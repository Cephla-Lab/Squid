"""Tests for the Lumencor Celesta HTTP driver."""

import socket

import pytest

import control.celesta as celesta


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def test_httpcommand_passes_a_timeout_to_urlopen(monkeypatch):
    seen = {}

    def fake_urlopen(url, *args, **kwargs):
        seen["url"] = url
        seen["timeout"] = kwargs.get("timeout")
        return _FakeResponse(b"{'message': 'A IP 192.168.201.200'}")

    monkeypatch.setattr(celesta.urllib.request, "urlopen", fake_urlopen)

    msg = celesta.lumencor_httpcommand(command="GET IP", ip="192.168.201.200")

    assert msg == {"message": "A IP 192.168.201.200"}
    assert seen["url"] == "http://192.168.201.200/service/?command=GET%20IP"
    assert seen["timeout"] is not None, "urlopen must be given a timeout so an unreachable Celesta cannot hang startup"
    assert 0 < seen["timeout"] <= 30


def test_httpcommand_honours_explicit_timeout(monkeypatch):
    seen = {}

    def fake_urlopen(url, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _FakeResponse(b"{'message': 'A IP 192.168.201.200'}")

    monkeypatch.setattr(celesta.urllib.request, "urlopen", fake_urlopen)

    celesta.lumencor_httpcommand(command="GET IP", ip="192.168.201.200", timeout=1.5)

    assert seen["timeout"] == 1.5


def test_celesta_init_marks_device_offline_when_connection_times_out(monkeypatch):
    def hanging_urlopen(url, *args, **kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr(celesta.urllib.request, "urlopen", hanging_urlopen)

    dev = celesta.CELESTA()

    assert dev.live is False
    assert dev.get_status() is False


def test_celesta_init_uses_configured_timeout_for_every_request(monkeypatch):
    timeouts = []

    responses = {
        "GET IP": b"{'message': 'A IP 192.168.201.200'}",
        "GET CHMAP": b"{'message': 'A CHMAP 405 445 488 518 545 640 730'}",
        "GET MAXINT": b"{'message': 'A MAXINT 1000'}",
    }

    def fake_urlopen(url, *args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        command = url.split("command=")[1].replace("%20", " ")
        if command.startswith("GET CH "):
            return _FakeResponse(b"{'message': 'A CH 0'}")
        if command.startswith("SET CH "):
            return _FakeResponse(b"{'message': 'A CH 0'}")
        if command.startswith("SET TTLENABLE "):
            return _FakeResponse(b"{'message': 'A TTLENABLE " + command[-1:].encode() + b"'}")
        return _FakeResponse(responses[command])

    monkeypatch.setattr(celesta.urllib.request, "urlopen", fake_urlopen)

    dev = celesta.CELESTA(timeout=2.0)

    assert dev.live is True
    assert dev.n_lasers == 7
    assert timeouts, "expected at least one HTTP request during init"
    assert all(t == 2.0 for t in timeouts)
