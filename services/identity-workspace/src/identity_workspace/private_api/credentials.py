"""Per-service secret mapping, using fixed-size constant-time digest comparison."""
import hashlib
import hmac
import re

from identity_workspace.errors import APIError

KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,128}")
CALLERS = {"introspect": frozenset({"task", "notification"}), "workspace_access": frozenset({"task"})}


class ServiceKeyConfigurationError(RuntimeError):
    """Safe to surface: only controlled setting names, never paths or values."""


def read_key_file(path):
    failed = False
    try:
        with path.open("rb") as stream:
            raw = stream.read(131)
            value = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        if len(raw) > 130 or KEY_PATTERN.fullmatch(value) is None:
            failed = True
    except (OSError, UnicodeError):
        failed = True
    if failed:
        raise ServiceKeyConfigurationError("Invalid service-key file. Check IDENTITY_*_SERVICE_KEY_FILE; values omitted.") from None
    return value


class ServiceKeyRegistry:
    def __init__(self, settings):
        self._digests = {}
        for caller in ("task", "notification"):
            value = getattr(settings, f"{caller}_service_key")
            path = getattr(settings, f"{caller}_service_key_file")
            if value is not None and path is not None:
                raise ServiceKeyConfigurationError("Choose IDENTITY_*_SERVICE_KEY or its _FILE setting, not both; values omitted.")
            raw = read_key_file(path) if path is not None else (value.get_secret_value() if value is not None else None)
            if raw is not None:
                self._digests[caller] = hashlib.sha256(raw.encode("ascii")).digest()
        if len(self._digests) == 2 and hmac.compare_digest(self._digests["task"], self._digests["notification"]):
            raise ServiceKeyConfigurationError("Task and Notification service keys must be distinct; values omitted.")

    def authorize(self, values, operation):
        if len(values) != 1 or KEY_PATTERN.fullmatch(values[0]) is None:
            raise APIError(401, code="SERVICE_CREDENTIAL_INVALID")
        presented = hashlib.sha256(values[0].encode("ascii")).digest()
        caller = None
        # Compare every configured fixed-size digest; no trusted caller-name header.
        for name, digest in self._digests.items():
            if hmac.compare_digest(presented, digest):
                caller = name
        if caller is None:
            raise APIError(401, code="SERVICE_CREDENTIAL_INVALID")
        if caller not in CALLERS[operation]:
            raise APIError(403)
        return caller
