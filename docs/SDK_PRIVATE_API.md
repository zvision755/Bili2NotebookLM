# notebooklm-py 私有接口依赖清单

本项目依赖**非官方**库 `notebooklm-py`（固定为 **0.8.6**，MIT）。它的公开 API 不足以保证上传可靠，所以我们用了少量**私有内部接口**。这些改动解决的是真实线上问题，**不应为了"代码更简洁"而删除**：

| 现象（实测） | 原因 | 对策 |
|---|---|---|
| 大文件（≥约 100 KB）上传最后一步约 8 秒后被断开，3 MB 以上服务端也收不全 | SDK 用**单个请求**发整个文件体 | 分块断点续传（`notebook/chunked_upload.py`） |
| 重复创建空笔记本；每次上传多出一个卡住的"孪生"来源 | libcurl 在"被复用的长连接无响应断开"时**静默重发 POST**，而 Google 常已处理了第一次 | 对非幂等写入强制新连接（`adapter._fresh_connections`） |
| 创建笔记本被 SDK 内部重试再发一次 | SDK 对服务端错误自动重试 | `server_error_max_retries=0`，由本项目先核对云端再决定 |

## 私有接口清单

> 当前版本：notebooklm-py **0.8.6**（唯一经过真实验证的版本）。清单的机器可读版本是 `notebook/sdk_compat.py` 里的 `PRIVATE_API_INVENTORY`，由契约测试守护。

| # | 依赖的接口 | 用途 | 升级风险 |
|---|---|---|---|
| 1 | `client.sources._uploader`（`SourceUploadPipeline` 实例） | 把"单请求上传文件体"替换为分块上传 | 属性改名/移除 → 无法替换 |
| 2 | `SourceUploadPipeline.upload_file_streaming(upload_url, file_obj, *, filename, on_progress, total_bytes, logger, expected_epoch)` | 被替换的方法，签名必须一致 | 参数变化 → 调用不兼容 |
| 3 | `SourceUploadPipeline._client_factory / _live_cookies / _authuser_header / _resolve_upload_timeout / _track_transport_client / assert_epoch` | 与 SDK 一样为每个分块构造带登录会话的 HTTP 客户端 | 任一改名 → 无法构造客户端 |
| 4 | `notebooklm._web.sources.upload`: `_validate_resumable_upload_url`、`_upload_url_origin`、`raise_for_upload_status`、`maybe_await_callback` | 校验续传 URL（防 SSRF）、设置 Origin/Referer、分类 HTTP 错误、进度回调 | 函数移除/语义变化 → 安全校验或错误分类失效 |
| 5 | `notebooklm._curl_cffi_transport.CurlCffiAsyncClient.__init__(session_factory=…)` | 注入 libcurl 的 `FRESH_CONNECT`/`FORBID_REUSE`（只作用于上下文内创建的 SDK 客户端） | 不再接受 `session_factory` → 无法限定作用范围 |
| 6 | `curl_cffi.CurlOpt.FRESH_CONNECT/FORBID_REUSE`、`AsyncSession(curl_options=…)` | libcurl 选项本身 | curl_cffi 大版本变化 |
| 7 | `NotebookLMClient.from_storage(server_error_max_retries=…)`（公开参数，但版本相关） | 关闭创建笔记本的 SDK 内部重试 | 参数被移除 → 创建可能被重复发送 |
| 8 | 异常上的 `.source_id`（注册之后失败时由 SDK 附带） | 孤儿来源的归属证据之一（尽力而为，缺失可容忍） | 缺失 → 仍有"调用期间新出现的同名卡住来源"作为证据 |
| 9 | 环境变量 `NOTEBOOKLM_TRANSPORT=curl_cffi` | 浏览器指纹传输；纯 httpx 在上传端点被断开 | SDK 将其标注为试验性质 |

## SDK 不兼容时的行为（安全失败）

在**任何云端写入之前**（创建笔记本、上传、清理孤儿），`sdk_compat.require()` 会检查版本与上述接口，不满足就返回 `SDK_INCOMPATIBLE`（退出码 26）并说明原因：

- 不会退回不可靠的单请求上传；不会假报上传成功；不会盲目重试。
- 版本不是 0.8.6：默认拒绝。若确认私有接口未变，可设 `BILI2NB_ALLOW_UNVERIFIED_SDK=1`——**接口检查仍会执行**，接口不匹配无法绕过。
- `bili2nb doctor` 的 `sdk-compat` 项会提前报告；`tests/unit/test_v11_hardening.py::test_installed_sdk_passes_the_compatibility_contract` 让 CI 在依赖升级破坏接口时直接失败。

## 升级 notebooklm-py 前的检查清单

1. 阅读新版本的 release notes，重点看上传管线、传输层、重试策略的变化。
2. 在分支里改 `pyproject.toml` 与 `sdk_compat.PINNED_SDK`，运行 `uv run pytest`（契约测试会列出具体哪个私有接口变了）。
3. 逐项核对上表，必要时调整 `chunked_upload.py` / `adapter.py`。
4. 在测试账号里用**真实小视频**做一次完整导入，并重复导入一次确认不重复建本、来源均 READY。
5. 更新本文件和 `docs/TEST_REPORT.md`。

## 孤儿来源的归属规则

失败的上传可能在云端留下卡住的来源（PREPARING / ERROR）。**只凭"同名 + 状态卡住"不足以认定它属于本工具**（可能是用户自己上传的同名文件）。允许删除的证据只有：

1. 任务记录里持久化的来源 ID（`jobs.orphans`，来自本任务之前失败的尝试）；
2. **本次调用期间**新出现的（不在调用前快照中）、带本文件名/标题、且卡住的来源；
3. SDK 异常自带的 `source_id`。

READY/PROCESSING 的来源永远不删；删不掉的 ID 会记入任务记录，下次重试再清理。涉及的回归测试见 `tests/unit/test_v11_hardening.py`。
