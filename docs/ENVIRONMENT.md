# ENVIRONMENT（Gate 0 / Gate 1 记录）

> 采集日期：2026-11-08。全部为只读检查，未改动系统代理、DNS、现有项目；未登录 Google。

## 1. 宿主环境（只读检查结果）

| 项目 | 结果 |
|---|---|
| 架构 / OS | arm64（Apple Silicon）/ macOS 27.0.1 (build 26A434) |
| 系统 Python | `/opt/homebrew/bin/python3` = 3.15.2（过新，不用于项目） |
| 项目 Python | uv 管理的 CPython **3.12.15**（`.venv`，`requires-python >=3.11`） |
| uv | 0.13.7（`/opt/homebrew/bin/uv`） |
| yt-dlp（系统） | 2026.10.26（`/opt/homebrew/bin/yt-dlp`）；项目 venv 内同版本 2026.10.26 |
| ffmpeg / ffprobe | 9.0.2（`/opt/homebrew/bin/`），Homebrew 安装，未重复安装 |
| Git | 2.56.0；项目目录原本不是 git 仓库，已在项目目录内 `git init` |
| Homebrew | 7.0.12 |
| 磁盘 | `~/Projects` 所在卷 926 GiB，可用 132 GiB（86% 已用） |
| 系统/环境代理 | 无（HTTP/HTTPS/SOCKS/PAC 均未启用，无 `*_proxy` 环境变量）；未做任何修改 |
| 连通性（HEAD，15s 超时） | bilibili.com 200（0.74s）；b23.tv 404（根路径无内容，域名可达）；notebooklm.google.com 301；accounts.google.com 302；pypi.org 200；DNS 均 <10ms |
| 副作用说明 | 运行 `notebooklm doctor` 创建了空目录 `~/.notebooklm`（0700），其下尚无任何凭证文件 |

## 2. 项目依赖（`uv.lock` 锁定）

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| typer | 0.28.0 | MIT | CLI |
| yt-dlp | 2026.10.26 | Unlicense | B 站解析/下载 |
| notebooklm-py[browser,impersonate] | **0.8.6**（精确固定） | MIT | NotebookLM 非官方 SDK；browser extra 带 playwright 1.64.1 供登录；impersonate extra 带 curl-cffi 0.17.0（上传必须，见下） |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | 帧处理 |
| scenedetect | 0.7.2 | BSD-3 | 场景检测 |
| pillow | 13.0.0 | MIT-CMU | 图片 |
| reportlab | 4.5.1 | BSD | PDF 生成 |
| pypdf | 7.0.0 | BSD | PDF 校验 |
| pytest / ruff | dev group | — | 测试 / lint |

已知小问题：依赖树里 `opencv-python`（非 headless）也被装入（经 scenedetect 的依赖），与 headless 共存会争用 `cv2`。Gate 2 实现选帧时核实并处理（改为 `scenedetect` 的 headless 选项或排除）。

FFmpeg 许可注意：Homebrew 的 ffmpeg 为本机调用的系统工具，本项目不分发 ffmpeg。

## 3. notebooklm-py 0.8.6 核验（Gate 1 前半：只读，未联网调用 Google）

- 来源：PyPI 与 GitHub `teng-lin/notebooklm-py`；MIT；2026-10-29 发布 0.8.6（0.8.1→0.8.6 约每 2–3 周一版，迭代快，必须固定版本并做 contract test）。
- 官方声明为**非官方、使用未公开 Google API，可能随时失效**；Google 已将 NotebookLM 更名为 Gemini Notebook（2026-07），库称仍可用。
- 认证：`notebooklm login`（Playwright 打开浏览器由人工登录，保存 `storage_state.json` 到 `~/.notebooklm/profiles/<profile>/storage_state.json`）；`--browser chrome` 可用系统 Chrome；`notebooklm auth check [--test] [--passive] [--json]`；`notebooklm auth refresh`；`notebooklm auth logout`。本项目**不使用** `--master-token`、`--browser-cookies`（会读取浏览器 Cookie 库）。
- Python API（已用 `inspect` 核实签名）：
  - `NotebookLMClient.from_storage(path=None, timeout=30.0, profile=None, ...)`（异步上下文管理器）
  - `client.notebooks.create(title) -> Notebook`、`.get(id)`、`.get_or_none(id)`、`.list()`
  - `client.sources.add_file(notebook_id, file_path, mime_type=None, *, wait=False, wait_timeout=120.0, title=None, on_progress=None) -> Source`
  - `client.sources.list(notebook_id, ...)`、`.get(...)`、`.wait_until_ready(notebook_id, source_id, timeout=120.0, ...)`、`.wait_all_until_ready(...)`
  - `SourceStatus`：`PROCESSING=1, READY=2, ERROR=3, PREPARING=5, UNKNOWN=-1`
  - 异常：`AuthError, RateLimitError, ServerError, SourceTimeoutError, SourceProcessingError, SourceAddError, NetworkError ...`
- Notebook URL：尚待用真实 notebook_id 验证（预期 `https://notebooklm.google.com/notebook/<id>`），Gate 1 实测后更新。
- 尚未核验（需真实账号，待用户授权）：音频/PDF 实际接受的格式与大小上限、READY 实际耗时。
- 当前认证状态：`notebooklm doctor` → 未认证（预期，Gate 0 不要求登录）。

## 4. 风险
- 非官方 API 随时可能失效 → 全部调用封装在 `notebook/adapter.py`，版本固定。
- `~/.notebooklm/.../storage_state.json` 是高敏感凭证；`.gitignore` 已忽略 `storage_state*`、`*cookies*`、`.env*`。


## 5. 实测补充（Gate 1–2）

- **NotebookLM 上传必须用 `curl_cffi` 传输**：默认 `httpx` 传输在本机网络上对上传端点反复 `Server disconnected without sending a response`（音频服务端已收但回执丢失；PDF 注册后未收字节，卡在 PREPARING）。设置 `NOTEBOOKLM_TRANSPORT=curl_cffi`（适配层默认启用）后一次成功。SDK 将其标注为 PoC，属风险项；可用环境变量覆盖。
- **NotebookLM 来源限制**（Google 帮助中心，2026-11-08 核对）：单个来源 500,000 词 / 上传文件 200 MB；本地音频含 m4a/mp3/wav；无语音的音频不支持。本项目 `MAX_UPLOAD_BYTES = 200 MB`。
- **B 站公开接口**：`GET https://api.bilibili.com/x/web-interface/view?bvid=…`（无需登录）返回 `pages[]`（cid/page/part/duration），用于 BV+CID 键与分 P 校验；yt-dlp 本身不暴露 CID。风控返回码 -412/-352/-799 不视为视频不存在。
- yt-dlp 对 B 站无需 Cookie 即可取得 `30280`（AAC ~192kbps）等独立 m4a 音频流与 ≤1080p 视频流；本项目仅取音频流 + ≤720p 视频流。
- 运行数据位置：`~/Library/Application Support/Bili2NotebookLM/`（`work/` 临时、`output/` 默认输出；可用 `BILI2NB_HOME` 覆盖）。

## 6. 命令名与依赖核对（2026-11-09）
- 命令：`bili2nb`（短）与 `bili2notebooklm`（长，与项目名一致）。冲突检查：本机 `which` 均不存在；PyPI 上 `bili2nb`、`bili2notebooklm` 均 404（未被占用）；Homebrew 无同名公式。未检查 GitHub 上的同名项目与其他包管理器（npm/cargo 等）。
- `uv tool install .` 实际选用 Python **3.14.7**（不是开发用的 3.12.15）；两者均已跑全套测试（206 passed）和真实 convert，结果一致（17 张截图/2,365,614 字节 PDF）。
- 依赖树中 `scenedetect` 会拉入 `opencv-python`，与 `opencv-python-headless` 同时安装（同为 5.0.0.93，共用 `cv2` 目录）。当前工作正常，但属于潜在冲突点。
- **已消除 opencv 双装（2026-11-09）**：`pyproject.toml` 的 `[tool.uv] override-dependencies` 对 `uv sync` 生效；`uv tool install` 不读取该节，故另附 `overrides.txt`，安装命令为 `uv tool install . --overrides overrides.txt`。验证：开发环境、干净拷贝 `uv sync`、`uv tool install` 三处都只剩 `opencv-python-headless`；`--frames scene`（唯一用到 scenedetect 的路径）真实运行正常、无 warning；pytest 209 passed。注意：卸载 `opencv-python` 会删掉共用的 `cv2` 文件，因此处理开发环境时必须随后 `--reinstall-package opencv-python-headless`（已做）。
