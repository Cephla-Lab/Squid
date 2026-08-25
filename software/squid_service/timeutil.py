from datetime import datetime, timezone


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with millisecond precision and trailing Z (spec §2.4)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
