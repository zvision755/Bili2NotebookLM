"""Stable error codes and the single exception type used across bili2nb."""

from __future__ import annotations

EXIT_CODES = {
    "INVALID_URL": 10,
    "VIDEO_UNAVAILABLE": 11,
    "MEDIA_ERROR": 12,
    "TOOL_MISSING": 13,
    "DISK_FULL": 14,
    "AUTH_REQUIRED": 20,
    "NOTEBOOK_CREATE_FAILED": 21,
    "UPLOAD_FAILED": 22,
    "SOURCE_NOT_READY": 23,
    "RATE_LIMITED": 24,
    "NETWORK_ERROR": 25,
    "SDK_INCOMPATIBLE": 26,
    "UPLOAD_PLAN_INFEASIBLE": 27,
    "PARTS_CHANGED": 28,
    "INTERRUPTED": 130,
    "JOB_NOT_FOUND": 30,
    "INTERNAL": 1,
}


class Bili2nbError(Exception):
    """Error with a stable code. Never put credentials in `message`/`detail`."""

    def __init__(
        self, code: str, message: str, *, detail: str | None = None, extra: dict | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.extra = extra or {}  # job_id/notebook_id/... shown in --json failures (no secrets)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.code, 1)
