import re

from squid_service.timeutil import utc_now_iso


def test_utc_now_iso_format():
    ts = utc_now_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", ts)
