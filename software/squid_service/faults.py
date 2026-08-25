"""Canonical fault shapes for the Squid Core Service (spec §2.5).

This module is the single source of truth for fault categories and codes.
Codes are allocated in 1000-blocks per category: 1xxx PROTOCOL ... 8xxx AUTOFOCUS.
"""

import itertools
import threading
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from squid_service.timeutil import utc_now_iso


class FaultCategory(str, Enum):
    PROTOCOL = "PROTOCOL"
    INVALID_PARAM = "INVALID_PARAM"
    CONFIG = "CONFIG"
    HARDWARE_TRANSIENT = "HARDWARE_TRANSIENT"
    HARDWARE_FAULT = "HARDWARE_FAULT"
    ACQUISITION = "ACQUISITION"
    IO = "IO"
    AUTOFOCUS = "AUTOFOCUS"


class SchedulerAction(str, Enum):
    RETRY = "RETRY"
    ABORT_PLATE = "ABORT_PLATE"
    REJECT_PLATE = "REJECT_PLATE"
    PAUSE_INSTRUMENT = "PAUSE_INSTRUMENT"
    ESCALATE_OPERATOR = "ESCALATE_OPERATOR"


# --- Code allocation (1000-block per category) ---
PROTOCOL_UNKNOWN_RESOURCE = 1001
PROTOCOL_WRONG_STATE = 1002
PROTOCOL_SCHEMA_VIOLATION = 1003
PROTOCOL_AUTH = 1004
PROTOCOL_FORBIDDEN = 1005
PROTOCOL_NOT_IMPLEMENTED = 1006
INVALID_PARAM_OUT_OF_RANGE = 2001
INVALID_PARAM_BAD_VALUE = 2002
CONFIG_UNKNOWN_CHANNEL = 3001
CONFIG_UNKNOWN_OBJECTIVE = 3002
CONFIG_CAPABILITY_MISSING = 3003
CONFIG_HARDWARE_MISMATCH = 3004
HARDWARE_TRANSIENT_TIMEOUT = 4001
HARDWARE_FAULT_GENERIC = 5001
HARDWARE_FAULT_INTERNAL = 5999
ACQUISITION_START_FAILED = 6001
ACQUISITION_RUNTIME = 6002
IO_PATH_NOT_WRITABLE = 7001
IO_DISK_FULL = 7002
IO_GENERIC = 7003
AUTOFOCUS_FAILURE = 8001
AUTOFOCUS_NOT_READY = 8002


class Fault(BaseModel):
    category: FaultCategory
    code: int
    recoverable: bool
    scheduler_action: SchedulerAction
    sequence: int = 0
    component: Optional[str] = None
    message: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str
    terminal: bool
    operator_intervention_required: bool = False
    plate_removable: bool = True
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None


def make_fault(
    category: FaultCategory,
    code: int,
    message: str,
    *,
    recoverable: bool = False,
    scheduler_action: SchedulerAction = SchedulerAction.ESCALATE_OPERATOR,
    terminal: bool = False,
    component: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Fault:
    return Fault(
        category=category,
        code=code,
        recoverable=recoverable,
        scheduler_action=scheduler_action,
        component=component,
        message=message,
        detail=detail or {},
        timestamp=utc_now_iso(),
        terminal=terminal,
    )


class FaultError(Exception):
    """Raise anywhere in the service layer; transports map it to a canonical response."""

    def __init__(self, fault: Fault):
        super().__init__(fault.message)
        self.fault = fault


# HTTP mapping is advisory triage only (spec §2.5); drivers branch on category/code.
_PROTOCOL_STATUS = {
    PROTOCOL_UNKNOWN_RESOURCE: 404,
    PROTOCOL_WRONG_STATE: 409,
    PROTOCOL_SCHEMA_VIOLATION: 422,
    PROTOCOL_AUTH: 401,
    PROTOCOL_FORBIDDEN: 403,
    PROTOCOL_NOT_IMPLEMENTED: 501,
}


def http_status_for(fault: Fault) -> int:
    if fault.category == FaultCategory.PROTOCOL:
        return _PROTOCOL_STATUS.get(fault.code, 422)
    if fault.category == FaultCategory.INVALID_PARAM:
        return 400
    if fault.category == FaultCategory.CONFIG:
        return 422
    if fault.category in (FaultCategory.HARDWARE_TRANSIENT, FaultCategory.HARDWARE_FAULT):
        return 500 if fault.code == HARDWARE_FAULT_INTERNAL else 503
    if fault.category == FaultCategory.ACQUISITION:
        return 503
    if fault.category == FaultCategory.IO:
        return 507 if fault.code == IO_DISK_FULL else 500
    if fault.category == FaultCategory.AUTOFOCUS:
        return 503
    return 500


class FaultLog:
    """Thread-safe fault history with monotonic sequence numbers."""

    def __init__(self, max_entries: int = 1000):
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._entries: List[Fault] = []
        self._max = max_entries

    def record(self, fault: Fault) -> Fault:
        with self._lock:
            stamped = fault.model_copy(update={"sequence": next(self._seq)})
            self._entries.append(stamped)
            if len(self._entries) > self._max:
                self._entries = self._entries[-self._max :]
            return stamped

    def since(self, seq: int, limit: int = 100) -> List[Fault]:
        with self._lock:
            return [f for f in self._entries if f.sequence > seq][:limit]

    @property
    def latest(self) -> Optional[Fault]:
        with self._lock:
            return self._entries[-1] if self._entries else None
