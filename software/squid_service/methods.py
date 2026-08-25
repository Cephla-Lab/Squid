"""Named acquisition-method registry (URS API-METH-001..005).

A method is an acquisition YAML stored server-side; clients reference it by name.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import List

import yaml

from control.acquisition_yaml_loader import parse_acquisition_yaml
from squid_service import faults as F

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


def _unknown_method(name: str) -> F.FaultError:
    return F.FaultError(
        F.make_fault(
            F.FaultCategory.PROTOCOL,
            F.PROTOCOL_UNKNOWN_RESOURCE,
            f"Unknown method: {name!r}",
            detail={"method": name},
        )
    )


def _invalid_name(name: str) -> F.FaultError:
    return F.FaultError(
        F.make_fault(
            F.FaultCategory.INVALID_PARAM,
            F.INVALID_PARAM_BAD_VALUE,
            f"Invalid method name: {name!r} (allowed: letters, digits, _ and -)",
            detail={"method": name},
        )
    )


class MethodRegistry:
    def __init__(self, methods_dir: Path):
        self._dir = Path(methods_dir)

    def path_for(self, name: str) -> Path:
        if not _NAME_RE.match(name or ""):
            raise _invalid_name(name)
        path = self._dir / f"{name}.yaml"
        if not path.exists():
            raise _unknown_method(name)
        return path

    def exists(self, name: str) -> bool:
        return bool(_NAME_RE.match(name or "")) and (self._dir / f"{name}.yaml").exists()

    def list(self) -> List[dict]:
        summaries = []
        if not self._dir.is_dir():
            return summaries
        for path in sorted(self._dir.glob("*.yaml")):
            summaries.append(self._summarize(path.stem, path))
        return summaries

    def get(self, name: str) -> dict:
        path = self.path_for(name)
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return {"name": name, "config": config}

    def save(self, name: str, config: dict, overwrite: bool) -> None:
        if not _NAME_RE.match(name or ""):
            raise _invalid_name(name)
        exists = (self._dir / f"{name}.yaml").exists()
        if exists and not overwrite:
            raise F.FaultError(
                F.make_fault(
                    F.FaultCategory.INVALID_PARAM,
                    F.INVALID_PARAM_BAD_VALUE,
                    f"Method {name!r} already exists (use PUT to update)",
                    detail={"method": name},
                )
            )
        if not exists and overwrite:
            raise _unknown_method(name)
        self._validate_config(config)
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._dir / f"{name}.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f)

    def delete(self, name: str) -> None:
        os.remove(self.path_for(name))

    def _validate_config(self, config: dict) -> None:
        """Parse-level validation via the canonical loader (no hardware access)."""
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
                yaml.safe_dump(config, tmp)
                tmp_path = tmp.name
            try:
                parse_acquisition_yaml(tmp_path)
            finally:
                os.unlink(tmp_path)
        except F.FaultError:
            raise
        except Exception as e:
            raise F.FaultError(
                F.make_fault(
                    F.FaultCategory.INVALID_PARAM,
                    F.INVALID_PARAM_BAD_VALUE,
                    f"Method configuration invalid: {e}",
                )
            )

    def _summarize(self, name: str, path: Path) -> dict:
        try:
            data = parse_acquisition_yaml(str(path))
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            return {
                "name": name,
                "widget_type": data.widget_type,
                "channels": data.channel_names,
                "objective": data.objective_name,
                "wellplate_format": raw.get("sample", {}).get("wellplate_format"),
                "wells": data.wells,
                "nz": data.nz,
                "nt": data.nt,
                "estimated_duration_s": None,  # best-effort placeholder, documented
            }
        except Exception as e:
            return {"name": name, "error": f"unparseable: {e}"}
