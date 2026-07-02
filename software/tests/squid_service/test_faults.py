from squid_service import faults as F


def test_make_fault_shape():
    f = F.make_fault(F.FaultCategory.INVALID_PARAM, F.INVALID_PARAM_OUT_OF_RANGE, "x out of range")
    d = f.model_dump()
    for key in (
        "category",
        "code",
        "recoverable",
        "scheduler_action",
        "sequence",
        "component",
        "message",
        "detail",
        "timestamp",
        "terminal",
        "operator_intervention_required",
        "plate_removable",
        "resolved_at",
        "resolved_by",
    ):
        assert key in d
    assert d["category"] == "INVALID_PARAM"
    assert d["code"] == 2001
    assert d["timestamp"].endswith("Z")


def test_http_status_mapping():
    cases = [
        (F.FaultCategory.PROTOCOL, F.PROTOCOL_UNKNOWN_RESOURCE, 404),
        (F.FaultCategory.PROTOCOL, F.PROTOCOL_WRONG_STATE, 409),
        (F.FaultCategory.PROTOCOL, F.PROTOCOL_SCHEMA_VIOLATION, 422),
        (F.FaultCategory.PROTOCOL, F.PROTOCOL_AUTH, 401),
        (F.FaultCategory.PROTOCOL, F.PROTOCOL_FORBIDDEN, 403),
        (F.FaultCategory.PROTOCOL, F.PROTOCOL_NOT_IMPLEMENTED, 501),
        (F.FaultCategory.INVALID_PARAM, F.INVALID_PARAM_OUT_OF_RANGE, 400),
        (F.FaultCategory.CONFIG, F.CONFIG_UNKNOWN_CHANNEL, 422),
        (F.FaultCategory.HARDWARE_TRANSIENT, F.HARDWARE_TRANSIENT_TIMEOUT, 503),
        (F.FaultCategory.HARDWARE_FAULT, F.HARDWARE_FAULT_GENERIC, 503),
        (F.FaultCategory.HARDWARE_FAULT, F.HARDWARE_FAULT_INTERNAL, 500),
        (F.FaultCategory.ACQUISITION, F.ACQUISITION_RUNTIME, 503),
        (F.FaultCategory.IO, F.IO_DISK_FULL, 507),
        (F.FaultCategory.IO, F.IO_GENERIC, 500),
        (F.FaultCategory.AUTOFOCUS, F.AUTOFOCUS_FAILURE, 503),
    ]
    for category, code, expected in cases:
        fault = F.make_fault(category, code, "msg")
        assert F.http_status_for(fault) == expected, (category, code)


def test_fault_log_sequences_and_since():
    log = F.FaultLog()
    f1 = log.record(F.make_fault(F.FaultCategory.IO, F.IO_GENERIC, "one"))
    f2 = log.record(F.make_fault(F.FaultCategory.IO, F.IO_GENERIC, "two"))
    assert (f1.sequence, f2.sequence) == (1, 2)
    assert log.latest.message == "two"
    assert [f.message for f in log.since(1)] == ["two"]
    assert log.since(2) == []


def test_fault_error_carries_fault():
    fault = F.make_fault(F.FaultCategory.CONFIG, F.CONFIG_UNKNOWN_CHANNEL, "nope")
    err = F.FaultError(fault)
    assert err.fault is fault
    assert "nope" in str(err)
