"""Compatibility guard for the unofficial `notebooklm-py` SDK.

bili2nb depends on a few SDK *private* internals (see docs/SDK_PRIVATE_API.md) to make uploads
reliable and to avoid duplicate writes. If a dependency bump changes any of them, the safe
behaviour is to STOP before any cloud write and say why — never to silently fall back to an
unreliable path or to report an upload as successful.

`check()` returns a list of human-readable problems (empty = compatible). It only inspects
classes/modules/signatures; it never connects to the network or instantiates a client.
"""

from __future__ import annotations

import functools
import inspect
import os
from importlib import metadata

from bili2nb.errors import Bili2nbError

PINNED_SDK = "0.8.6"  # the only version verified live (see docs/TEST_REPORT.md)

#: What we rely on, and why. Kept as data so docs and tests share one source of truth.
PRIVATE_API_INVENTORY: list[dict[str, str]] = [
    {"key": "_uploader", "api": "client.sources._uploader  (SourceUploadPipeline instance)",
     "why": "replace the single-request body upload with chunked, resumable upload"},
    {"key": "upload_file_streaming", "api": "SourceUploadPipeline.upload_file_streaming(upload_url, file_obj, *, filename, on_progress, total_bytes, logger, expected_epoch)",
     "why": "the method we replace; signature must match exactly"},
    {"key": "_client_factory", "api": "SourceUploadPipeline._client_factory / _live_cookies / _authuser_header / _resolve_upload_timeout / _track_transport_client / assert_epoch",
     "why": "build an authenticated HTTP client for each chunk like the SDK does"},
    {"key": "_validate_resumable_upload_url", "api": "notebooklm._web.sources.upload: _validate_resumable_upload_url, _upload_url_origin, raise_for_upload_status, maybe_await_callback",
     "why": "validate the resumable URL (SSRF guard), set Origin/Referer, classify HTTP errors, progress callback"},
    {"key": "session_factory", "api": "notebooklm._curl_cffi_transport.CurlCffiAsyncClient.__init__(session_factory=...)",
     "why": "inject libcurl FRESH_CONNECT/FORBID_REUSE so libcurl cannot silently re-send a POST"},
    {"key": "FRESH_CONNECT", "api": "curl_cffi.CurlOpt.FRESH_CONNECT / FORBID_REUSE; AsyncSession(curl_options=...)",
     "why": "the libcurl options themselves"},
    {"key": "server_error_max_retries", "api": "NotebookLMClient.from_storage(server_error_max_retries=...)",
     "why": "disable the SDK's own retry of the non-idempotent notebook create"},
    {"key": "source_id", "api": "exceptions carry .source_id after registration (NetworkError / SourceAddError)",
     "why": "ownership evidence for an orphan source (best effort; absence is tolerated)"},
    {"key": "NOTEBOOKLM_TRANSPORT", "api": "env NOTEBOOKLM_TRANSPORT=curl_cffi",
     "why": "browser-fingerprint transport; plain httpx was disconnected by the upload endpoint"},
]

_STREAM_PARAMS = ("upload_url", "file_obj", "filename", "on_progress", "total_bytes", "logger", "expected_epoch")
_PIPELINE_ATTRS = ("upload_file_streaming", "_client_factory", "_live_cookies", "_authuser_header",
                   "_resolve_upload_timeout", "_track_transport_client", "assert_epoch")
_UPLOAD_MODULE_NAMES = ("SourceUploadPipeline", "_validate_resumable_upload_url", "_upload_url_origin",
                        "raise_for_upload_status", "maybe_await_callback")


def _installed_version() -> str:
    try:
        return metadata.version("notebooklm-py")
    except metadata.PackageNotFoundError:
        return "未安装"


def _params(fn) -> set[str]:
    try:
        return set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return set()


def _capability_problems() -> list[str]:
    problems: list[str] = []
    try:
        import notebooklm
        from notebooklm import _curl_cffi_transport as transport
        from notebooklm._web.sources import upload as up
    except Exception as exc:  # noqa: BLE001 - any import failure means "not what we verified"
        return [f"无法导入 notebooklm 内部模块（{type(exc).__name__}）：SDK 结构已变化"]

    for name in _UPLOAD_MODULE_NAMES:
        if not hasattr(up, name):
            problems.append(f"notebooklm._web.sources.upload.{name} 缺失")
    pipeline = getattr(up, "SourceUploadPipeline", None)
    if pipeline is not None:
        for attr in _PIPELINE_ATTRS:
            if not hasattr(pipeline, attr):
                problems.append(f"SourceUploadPipeline.{attr} 缺失")
        if hasattr(pipeline, "upload_file_streaming"):
            missing = [p for p in _STREAM_PARAMS if p not in _params(pipeline.upload_file_streaming)]
            if missing:
                problems.append(f"SourceUploadPipeline.upload_file_streaming 签名已变化（缺少参数 {', '.join(missing)}）")

    client_cls = getattr(transport, "CurlCffiAsyncClient", None)
    if client_cls is None or "session_factory" not in _params(client_cls.__init__):
        problems.append("CurlCffiAsyncClient 不再接受 session_factory，无法限定连接控制的作用范围")

    from_storage = getattr(getattr(notebooklm, "NotebookLMClient", None), "from_storage", None)
    if from_storage is None or "server_error_max_retries" not in _params(from_storage):
        problems.append("NotebookLMClient.from_storage 不再支持 server_error_max_retries（无法关闭创建笔记本的内部重试）")

    try:
        import curl_cffi
        from curl_cffi.requests import AsyncSession

        for opt in ("FRESH_CONNECT", "FORBID_REUSE"):
            if not hasattr(curl_cffi.CurlOpt, opt):
                problems.append(f"curl_cffi.CurlOpt.{opt} 缺失（无法阻止 libcurl 静默重发 POST）")
        # AsyncSession forwards **kwargs to BaseSession, which declares curl_options.
        if not any("curl_options" in _params(c.__init__) for c in AsyncSession.__mro__):
            problems.append("curl_cffi.AsyncSession 不再接受 curl_options")
    except ImportError:
        problems.append("curl_cffi 未安装（上传必须使用 curl_cffi 传输）")
    return problems


@functools.lru_cache(maxsize=1)
def check() -> list[str]:
    """Problems that make unattended cloud writes unsafe (empty list = OK). Cached per process."""
    problems = list(_capability_problems())
    ver = _installed_version()
    if ver != PINNED_SDK and not os.environ.get("BILI2NB_ALLOW_UNVERIFIED_SDK"):
        problems.append(
            f"notebooklm-py 版本 {ver} 未经验证（本项目只验证过 {PINNED_SDK}）；"
            "如确认私有接口未变，可设置 BILI2NB_ALLOW_UNVERIFIED_SDK=1（接口检查仍会执行）"
        )
    return problems


def require(operation: str = "write") -> None:
    """Raise SDK_INCOMPATIBLE before any cloud write if the SDK is not what we verified."""
    problems = check()
    if problems:
        raise Bili2nbError(
            "SDK_INCOMPATIBLE",
            "NotebookLM SDK 与本工具验证过的版本不兼容，已在任何云端写入之前停止："
            + "；".join(problems[:3]),
            detail=f"operation={operation}; " + " | ".join(problems),
        )
