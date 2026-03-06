"""
Structured run logger for orchestrated experiments.

Writes append-only event logs plus lightweight CSV/JSON rollups that are
usable for postrun analysis and QC.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Optional

import squid.core.logging
from squid.backend.controllers.multipoint import FovTaskCompleted, FovTaskStarted
from squid.backend.controllers.orchestrator.state import (
    OrchestratorAttemptUpdate,
    OrchestratorInterventionRequired,
    OrchestratorProgress,
    OrchestratorRoundCompleted,
    OrchestratorRoundStarted,
    OrchestratorStateChanged,
    OrchestratorStepCompleted,
    OrchestratorStepStarted,
    OrchestratorTimingSnapshot,
    WarningRaised,
)
from squid.core.events import EventBus

_log = squid.core.logging.get_logger(__name__)


class RunLogger:
    """Write structured run and QC artifacts into an experiment directory."""

    def __init__(self, event_bus: EventBus, experiment_path: str) -> None:
        self._event_bus = event_bus
        self._experiment_path = experiment_path
        self._log_path = os.path.join(experiment_path, "orchestrator_run_log.jsonl")
        self._step_csv_path = os.path.join(experiment_path, "step_metrics.csv")
        self._fov_csv_path = os.path.join(experiment_path, "fov_metrics.csv")
        self._warning_summary_path = os.path.join(experiment_path, "warning_summary.json")
        self._timing_summary_path = os.path.join(experiment_path, "timing_summary.json")
        self._qc_summary_path = os.path.join(experiment_path, "qc_summary.json")
        self._subscriptions: list[tuple[type, object]] = []
        self._warning_counts: Counter[tuple[str, str]] = Counter()
        self._warning_rounds: defaultdict[str, set[str]] = defaultdict(set)
        self._step_rows: list[dict[str, Any]] = []
        self._fov_rows: list[dict[str, Any]] = []
        self._latest_progress: Optional[OrchestratorProgress] = None
        self._latest_timing: Optional[OrchestratorTimingSnapshot] = None
        self._final_state: str = ""

    def start(self) -> None:
        os.makedirs(self._experiment_path, exist_ok=True)
        for event_type, handler in (
            (OrchestratorStateChanged, self._on_event),
            (OrchestratorProgress, self._on_event),
            (OrchestratorRoundStarted, self._on_event),
            (OrchestratorRoundCompleted, self._on_event),
            (OrchestratorStepStarted, self._on_step_started),
            (OrchestratorStepCompleted, self._on_step_completed),
            (OrchestratorAttemptUpdate, self._on_event),
            (OrchestratorInterventionRequired, self._on_event),
            (OrchestratorTimingSnapshot, self._on_timing),
            (WarningRaised, self._on_warning),
            (FovTaskStarted, self._on_fov_started),
            (FovTaskCompleted, self._on_fov_completed),
        ):
            self._event_bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))

    def stop(self) -> None:
        for event_type, handler in self._subscriptions:
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        self._write_step_metrics()
        self._write_fov_metrics()
        self._write_warning_summary()
        self._write_timing_summary()
        self._write_qc_summary()

    def _serialize_event(self, event: object) -> dict[str, Any]:
        if is_dataclass(event):
            payload = asdict(event)
        else:
            payload = {
                key: value
                for key, value in vars(event).items()
                if not key.startswith("_")
            }
        payload["event_type"] = type(event).__name__
        payload["logged_at"] = datetime.now().isoformat()
        return payload

    def _append_event(self, event: object) -> None:
        try:
            with open(self._log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(self._serialize_event(event), default=str))
                handle.write("\n")
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("Failed writing run log event: %s", exc)

    def _on_event(self, event: object) -> None:
        if isinstance(event, OrchestratorStateChanged):
            self._final_state = event.new_state
        if isinstance(event, OrchestratorProgress):
            self._latest_progress = event
        self._append_event(event)
        self._write_timing_summary()
        self._write_qc_summary()

    def _on_timing(self, event: OrchestratorTimingSnapshot) -> None:
        self._latest_timing = event
        self._append_event(event)
        self._write_timing_summary()
        self._write_qc_summary()

    def _on_warning(self, event: WarningRaised) -> None:
        self._warning_counts[(event.category, event.severity)] += 1
        if event.round_name:
            self._warning_rounds[event.category].add(event.round_name)
        self._append_event(event)
        self._write_warning_summary()
        self._write_qc_summary()

    def _on_step_started(self, event: OrchestratorStepStarted) -> None:
        self._step_rows.append(
            {
                "experiment_id": event.experiment_id,
                "round_index": event.round_index,
                "step_index": event.step_index,
                "step_type": event.step_type,
                "estimated_seconds": event.estimated_seconds,
                "attempt": 1,
                "duration_seconds": "",
                "success": "",
                "error": "",
            }
        )
        self._append_event(event)
        self._write_step_metrics()
        self._write_qc_summary()

    def _on_step_completed(self, event: OrchestratorStepCompleted) -> None:
        for row in reversed(self._step_rows):
            if (
                row["round_index"] == event.round_index
                and row["step_index"] == event.step_index
                and row["step_type"] == event.step_type
                and row["duration_seconds"] == ""
            ):
                row["duration_seconds"] = event.duration_seconds
                row["success"] = event.success
                row["error"] = event.error or ""
                break
        self._append_event(event)
        self._write_step_metrics()
        self._write_qc_summary()

    def _on_fov_started(self, event: FovTaskStarted) -> None:
        self._fov_rows.append(
            {
                "fov_id": event.fov_id,
                "round_index": event.round_index,
                "time_point": event.time_point,
                "attempt": event.attempt,
                "fov_index": event.fov_index,
                "status": "RUNNING",
                "error_message": "",
            }
        )
        self._append_event(event)
        self._write_fov_metrics()
        self._write_qc_summary()

    def _on_fov_completed(self, event: FovTaskCompleted) -> None:
        for row in reversed(self._fov_rows):
            if (
                row["fov_id"] == event.fov_id
                and row["round_index"] == event.round_index
                and row["attempt"] == event.attempt
                and row["status"] == "RUNNING"
            ):
                row["status"] = event.status.name
                row["error_message"] = event.error_message or ""
                break
        self._append_event(event)
        self._write_fov_metrics()
        self._write_qc_summary()

    def _write_step_metrics(self) -> None:
        if not self._step_rows:
            return
        with open(self._step_csv_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self._step_rows[0].keys()))
            writer.writeheader()
            writer.writerows(self._step_rows)

    def _write_fov_metrics(self) -> None:
        if not self._fov_rows:
            return
        with open(self._fov_csv_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self._fov_rows[0].keys()))
            writer.writeheader()
            writer.writerows(self._fov_rows)

    def _write_warning_summary(self) -> None:
        payload = {
            "warning_counts": {
                f"{category}:{severity}": count
                for (category, severity), count in sorted(self._warning_counts.items())
            },
            "affected_rounds": {
                category: sorted(rounds)
                for category, rounds in self._warning_rounds.items()
            },
        }
        with open(self._warning_summary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def _write_timing_summary(self) -> None:
        if self._latest_timing is None and self._latest_progress is None:
            return
        payload = {
            "elapsed_seconds": getattr(self._latest_timing, "elapsed_seconds", getattr(self._latest_progress, "elapsed_seconds", 0.0)),
            "effective_run_seconds": getattr(self._latest_timing, "effective_run_seconds", getattr(self._latest_progress, "effective_run_seconds", 0.0)),
            "paused_seconds": getattr(self._latest_timing, "paused_seconds", getattr(self._latest_progress, "paused_seconds", 0.0)),
            "retry_overhead_seconds": getattr(self._latest_timing, "retry_overhead_seconds", getattr(self._latest_progress, "retry_overhead_seconds", 0.0)),
            "intervention_overhead_seconds": getattr(self._latest_timing, "intervention_overhead_seconds", getattr(self._latest_progress, "intervention_overhead_seconds", 0.0)),
            "eta_seconds": getattr(self._latest_timing, "eta_seconds", getattr(self._latest_progress, "eta_seconds", None)),
            "subsystem_seconds": getattr(self._latest_timing, "subsystem_seconds", {}),
        }
        with open(self._timing_summary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def _write_qc_summary(self) -> None:
        failed_fovs = sum(1 for row in self._fov_rows if row["status"] == "FAILED")
        skipped_fovs = sum(1 for row in self._fov_rows if row["status"] == "SKIPPED")
        payload = {
            "final_state": self._final_state,
            "step_attempts": len(self._step_rows),
            "fov_attempts": len(self._fov_rows),
            "failed_fovs": failed_fovs,
            "skipped_fovs": skipped_fovs,
            "warning_total": sum(self._warning_counts.values()),
            "retry_burden": max(0, len(self._fov_rows) - len({row["fov_id"] for row in self._fov_rows})),
            "timing_summary_path": os.path.basename(self._timing_summary_path),
            "warning_summary_path": os.path.basename(self._warning_summary_path),
        }
        with open(self._qc_summary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
