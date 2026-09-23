"""Tests for the Lumencor Celesta HTTP driver."""

import io

import pytest

import control.celesta as celesta

IP_REPLY = b"{'message': 'A IP 192.168.201.200'}"


@pytest.mark.parametrize("kwargs, expected", [({}, celesta.DEFAULT_TIMEOUT_S), ({"timeout": 1.5}, 1.5)])
def test_httpcommand_passes_timeout_to_urlopen(monkeypatch, kwargs, expected):
    seen = {}

    def fake_urlopen(url, data=None, timeout=None, *args, **kw):
        seen["url"], seen["timeout"] = url, timeout
        return io.BytesIO(IP_REPLY)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    msg = celesta.lumencor_httpcommand(command="GET IP", ip="192.168.201.200", **kwargs)

    assert msg == {"message": "A IP 192.168.201.200"}
    assert seen["url"] == "http://192.168.201.200/service/?command=GET%20IP"
    assert seen["timeout"] == expected


def test_offline_celesta_stops_after_one_timeout(monkeypatch):
    calls = []

    def hanging_urlopen(url, data=None, timeout=None, *args, **kw):
        calls.append(url)
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", hanging_urlopen)

    dev = celesta.CELESTA()

    assert dev.get_status() is False
    assert len(calls) == 1  # only the GET IP probe; nothing more is attempted once the device is known offline
    with pytest.raises(ConnectionError):
        dev.set_intensity(0, 10)
    assert len(calls) == 1


def test_celesta_init_uses_configured_timeout_for_every_request(monkeypatch):
    timeouts = []
    responses = {
        "GET IP": IP_REPLY,
        "GET CHMAP": b"{'message': 'A CHMAP 405 445 488 518 545 640 730'}",
        "GET MAXINT": b"{'message': 'A MAXINT 1000'}",
    }

    def fake_urlopen(url, data=None, timeout=None, *args, **kw):
        timeouts.append(timeout)
        command = url.split("command=")[1].replace("%20", " ")
        # GET CH / SET CH / SET TTLENABLE replies are only inspected for a leading "A" or trailing "0"/"1".
        return io.BytesIO(responses.get(command, b"{'message': 'A 0'}"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    dev = celesta.CELESTA(timeout=2.0)

    assert dev.get_status() is True
    assert set(timeouts) == {2.0}
