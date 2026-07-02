"""Service configuration, sourced from control._def (INI-backed)."""

import ipaddress

from pydantic import BaseModel, model_validator


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class ServiceConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5060
    auth_enabled: bool = False
    auth_token: str = ""
    methods_dir: str = "machine_configs/acquisition_methods"

    @model_validator(mode="after")
    def _require_auth_off_loopback(self) -> "ServiceConfig":
        # Deviation from spec §2.3 (auth default-on), documented in the design doc:
        # loopback binds may run without auth; anything else requires a bearer token.
        if not _is_loopback(self.host):
            if not self.auth_enabled or not self.auth_token:
                raise ValueError(
                    "CORE_SERVICE bound to a non-loopback host requires auth_enabled=true " "and a non-empty auth_token"
                )
        if self.auth_enabled and not self.auth_token:
            raise ValueError("auth_enabled requires a non-empty auth_token")
        return self

    @classmethod
    def from_def(cls) -> "ServiceConfig":
        import control._def

        return cls(
            host=getattr(control._def, "CORE_SERVICE_HOST", "127.0.0.1"),
            port=getattr(control._def, "CORE_SERVICE_PORT", 5060),
            auth_enabled=getattr(control._def, "CORE_SERVICE_AUTH_ENABLED", False),
            auth_token=getattr(control._def, "CORE_SERVICE_AUTH_TOKEN", ""),
            methods_dir=getattr(control._def, "CORE_SERVICE_METHODS_DIR", "machine_configs/acquisition_methods"),
        )
