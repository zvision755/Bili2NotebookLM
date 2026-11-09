# TEST_REPORT

本报告记录每个 Gate 的验证证据。PASS / FAIL / BLOCKED 仅基于可复现实测证据；mock 测试只证明逻辑，不代表真实 B 站 / NotebookLM 通过。

> **阅读指南**：「当前状态」一节是最新结论；之后的各 Gate 小节**按时间顺序保留完整的验证与故障记录**（含已修复的 BLOCKED / FAIL / 进行中状态和技术分析），其中的状态标签是**当时**的状态。真实笔记本与来源 ID 在公开文档中一律用别名（NB-* 为笔记本，SRC-* 为来源）代替，别名对应的真实笔记本不对外提供。

## 当前状态（V1.1）

| Gate | 当前结论 | 关键证据 |
|---|---|---|
| 0 环境与初始化 | PASS | Gate 0 节；ENVIRONMENT.md |
| 1 认证与真实双来源上传 | PASS | Gate 1 节（网页问答验证：音频与 PDF 中的暗号均被正确回答） |
| 2 convert 本地素材 | PASS | Gate 2 节 |
| 3 import 端到端 | PASS | Gate 3 节末“最终真实运行记录”：2 来源 READY、幂等复用、笔记本总数不变 |
| 4 可靠性 / 视觉 / 性能 | PASS | Gate 4 节：benchmark、召回 14/14、重复 0、故障注入、长/短视频真实 import、重复建本根因与修复验证 |
| 5 最终验收（V1.1 修订标准） | PASS | Gate 5 节：CLI 自动导入 + 两来源 READY + NotebookLM 网页版问答（16 题全部通过）；手机端不再是必验项 |

**平台验证范围**（见 V1.1_HARDENING.md 的实测记录）：
- macOS：完整真实验证（真实 Google 登录、真实 NotebookLM 导入、真实 B 站下载）。
- macOS / Windows / Linux：GitHub Actions 自动运行 Ruff、离线 Pytest、`doctor`、`uv tool install` 全局命令冒烟测试。
- Windows / Linux：**尚未**做真实 Google 登录与真实 NotebookLM 导入验证。

**自动化测试数量与最新 CI 结果**以 V1.1_HARDENING.md 的最终报告为准（避免多处文档数字不一致）。

---

## 历史验证记录（按时间顺序）

## Gate 0 — 环境可用与项目初始化

- 结果：**PASS**
- 日期 / 环境：2026-11-08；macOS 27.0.1 arm64；Python 3.12.15（uv venv）；代码版本：未提交的初始骨架 v0.1.0（git init 后尚无提交）
- 真实外部 API 调用：无（仅 curl HEAD 连通性与 PyPI 元数据查询；未触及 Google 账号）

| # | 条件 | 命令 / 证据 | 结果 |
|---|---|---|---|
| 1 | 记录 arch、Python≥3.11、uv、yt-dlp、ffmpeg、ffprobe、Git、磁盘、DNS/连通性 | 见 docs/ENVIRONMENT.md §1 | PASS |
| 2 | 隔离 venv 安装；`bili2nb --help` / `import --help` / `convert --help` exit 0 | `uv sync --python 3.12`（首次因网络 TLS 中断失败，重试成功）；三条 help 命令均 `exit=0`，显示 `url` 参数与 `--help` | PASS（见下注） |
| 3 | 无越权操作 | 未改代理/DNS/系统设置/其他项目；未登录 Google；唯一副作用为 `notebooklm doctor` 创建空目录 `~/.notebooklm`(0700) | PASS |
| 4 | .gitignore 覆盖凭证/Cookie/日志/音视频/PDF/SQLite | 创建 `.env a.sqlite storage_state.json cookies.txt x.mp4 y.m4a z.pdf app.log` 后 `git check-ignore -v` 全部命中规则后删除 | PASS |
| 5 | 不需要 Google 登录 | — | PASS |

发现并修复的缺陷：初版 `.gitignore` 中 `media/` 未锚定，会误忽略源码目录 `src/bili2nb/media/`；已改为 `/media/` 等根锚定规则，`git check-ignore src/bili2nb/media/audio.py` 验证不再被忽略，根目录 `media/` 仍被忽略。

注：Gate 0 的 CLI 仅为骨架，`convert`/`import` 目前 `exit 2` 占位，**其真实参数（--frames、--json 等）在 Gate 2/3 实现并带测试后才出现在 --help**（遵循 PRD“未做的选项不出现在 help”）。
自动化测试：0（Gate 0 无要求）。
遗留风险：opencv-python 与 headless 并存（见 ENVIRONMENT.md §2）。

---

## Gate 1 — NotebookLM 认证与真实双来源上传

- 结果：**PASS（带保留项）**。条件 1–4 均有真实证据；条件 5 的上传异常/会话失效诊断已在真实环境观察到并有离线测试，系统化错误注入留到 Gate 4。默认 httpx 传输下 PDF 上传三次失败；改用 SDK 的 `curl_cffi` 传输后成功。
- 日期 / 环境：2026-11-08；macOS 27.0.1 arm64；Python 3.12.15；notebooklm-py 0.8.6

已完成并实测（无需 Google 账号）：

| 项 | 证据 | 结果 |
|---|---|---|
| SDK 版本/许可证/方法签名核验 | `inspect.signature` + PyPI 元数据，见 ENVIRONMENT.md §3 | 完成 |
| NotebookAdapter 最小实现 | `src/bili2nb/notebook/adapter.py`：check_auth / create_notebook / upload_file(wait=False) / list_sources / wait_ready(READY 才返回) / notebook_url；SDK 异常映射为稳定错误码 | 代码完成，**未经真实账号验证** |
| `bili2nb auth login / check [--json]` | 命令存在；`login` 仅委托 SDK 的交互式浏览器登录，不读 Cookie | 代码完成 |
| 无会话时 AUTH_REQUIRED（真实命令，无 mock） | `bili2nb auth check` → `[AUTH_REQUIRED] NotebookLM 未登录或会话已失效。请运行: bili2nb auth login`，exit=20；`--json` stdout 恰为一行 JSON，不含敏感字段 | PASS（条件 1 的“无会话”半边） |
| 离线单元测试 | `pytest -q` → 11 passed / 0 failed / 0 skipped；`ruff check .` 通过；测试默认禁止 socket 连接 | PASS（仅逻辑，mock） |
| 测试素材 | `tmp/gate1/gate1_audio.m4a`（macOS `say` 合成英文语音，8.8s，AAC，76 KB，暗号 “green giraffe”）；`tmp/gate1/gate1_page.pdf`（1 页，可见文字暗号 “purple owl 7421”）。均非敏感，目录已被 .gitignore | 就绪 |
| 真实验证脚本 | `scripts/gate1_live.py`：幂等（状态存 `tmp/gate1/state.json`，重跑不会重复建本/重复上传），未登录时 exit 20 | 就绪 |

尚未完成（需要用户）：
1. 条件 1：用户本人完成 Google 登录后，`bili2nb auth check` 对真实会话返回成功。
2. 条件 2–3：经用户同意后创建 1 个测试笔记本、上传音频+PDF、取得不同 source_id、两者轮询至 READY。
3. 条件 4：网页端打开 notebook URL，各问一题（音频暗号、PDF 暗号）并记录。
4. 条件 5：上传超时场景的诊断（部分以注入错误测试，待 Gate 4 完善）。
5. 验证 notebook URL 格式 `https://notebooklm.google.com/notebook/<id>` 是否真实可打开。


### Gate 1 真实运行记录（用户已本人登录并明确同意创建 1 个测试笔记本、上传 2 个文件）

| 步骤 | 命令/动作 | 实际结果 |
|---|---|---|
| 登录 | 用户在本机终端执行 `bili2nb auth login --browser chrome` | `storage_state.json` 权限 0600，目录 0700，位于 `~/.notebooklm/profiles/default/`（不在项目内） |
| auth check（真实） | `bili2nb auth check` | **PASS**：`Auth OK (account has 23 notebooks)`，exit 0 |
| 创建笔记本 | `scripts/gate1_live.py` | **PASS**：notebook_id `NB-T`，耗时 9.5s（含 auth check） |
| 上传音频 | `sources.add_file(wait=False)` | SDK 报 `NetworkError (upload_finalize): Server disconnected without sending a response`；**但**随后列表显示该来源已存在且 **READY**（source `SRC-1`，word_count 26，READY 耗时 4.3s）。即“服务端已接收、响应被丢”，属 **误报失败** |
| 诊断重试（诊断操作造成） | 为查原因再次 `add_file` 同一音频 | 同样报错且服务端再次接收 → 笔记本内出现**重复**音频来源 `SRC-6`（READY）。教训：失败后必须先查询来源再重试 |
| 上传 PDF #1 | `add_file` | 报 `ADD_SOURCE_FILE … Server disconnected`（0.41s，SDK 内部重试用尽）；来源已注册（`SRC-5`）但**停在 PREPARING 超过 10 分钟**（字节从未上传，孤儿） |
| 上传 PDF #3 | 用户重新执行 `auth login` 得到全新会话（`auth check` 通过，24 本）后，经适配层再试一次 | 仍报断开连接；又产生孤儿 `SRC-7`（PREPARING）。**排除“会话过旧”原因** |
| 上传 PDF #4（改用 curl_cffi） | `NOTEBOOKLM_TRANSPORT=curl_cffi`，`pip` extra `impersonate`（curl-cffi 0.17.0），单次尝试 | **成功**：上传耗时 18.0s，source `SRC-2`，**READY**（等待 4.3s）；音频 `SRC-1` 同为 READY。两个不同 source_id，均 READY；`sources.list` 另显示 4 个 PREPARING 孤儿（其中 1 个 `SRC-4` 是 SDK 内部重试在某次失败中额外注册的，非人为主动上传） |
| 上传 PDF #2 | 经新适配层（对账后重试一次） | 仍报断开连接；又产生孤儿 `SRC-3`（PREPARING）。**PDF 尚无任何 READY 来源** |

结论与已验证事实：
1. 认证与创建笔记本链路可用；音频来源可达 READY。
2. NotebookLM 上传端点在本机网络上**间歇性断开连接且不返回响应**，服务端有时已落地（音频）、有时只注册未收字节（PDF，卡在 PREPARING）。**PDF 路径未打通 → Gate 1 条件 3 未满足，条件 4（网页问答）、条件 5 未执行。**
3. 适配层已修复为“上传前按文件名查重、网络类错误先对账再决定是否重传、孤儿来源不复用并在错误 detail 中报告”，新增 5 个离线测试（共 16 个全部通过，ruff 通过）。该修复尚未被一次成功的真实 PDF 上传验证。
4. 当前测试笔记本中遗留：1 个重复 READY 音频来源、3 个 PREPARING 孤儿 PDF 来源。**未删除任何云端内容**（待用户决定）。
5. 未验证：notebook URL `https://notebooklm.google.com/notebook/<notebook-id>` 是否可在网页打开（需用户目检）。

待决定的方案（均未执行）：A) 等待一段时间/换网络后再试一次 PDF；B) 启用 SDK 官方可选传输 `curl_cffi`（需安装 `notebooklm-py[impersonate]`，改变 TLS 指纹）；C) 先推进 Gate 2，同时保留 Gate 1 为 FAIL/BLOCKED。


### Gate 1 更新（curl_cffi 之后）
- 结论：PDF 上传失败的原因在**传输层**，不是账号/会话/文件问题；换成 curl_cffi（浏览器式 TLS 指纹）后一次成功。SDK 自己把该传输标注为 PoC，已记录为风险；适配层默认启用，可用环境变量 `NOTEBOOKLM_TRANSPORT=httpx` 覆盖。
- 离线测试：17 passed，ruff 通过。
- 测试笔记本现状：2 个 READY 音频（含 1 个诊断操作造成的重复）、1 个 READY PDF、4 个 PREPARING 孤儿。均未删除。
- **仍需用户**：在网页打开测试笔记本，对音频问“暗号是什么”（应答 green giraffe）、对 PDF 问“PDF 暗号是什么”（应答 purple owl 7421），并回告结果；同时确认网址能打开。


### Gate 1 用户网页核验（2026-11-08，用户本人在 NotebookLM 网页完成，提供截图）
- 笔记本网址 `https://notebooklm.google.com/notebook/<id>` 格式**真实可打开**（截图地址栏确认）。
- 问“暗号是什么” → 回答“录音中的暗号是 green giraffe”（带来源引用）→ **音频转录与问答 PASS**。
- 问“PDF 里的暗号是什么” → 回答“purple owl 7421”（带来源引用）→ **PDF 文字问答 PASS**。
- 截图还显示：2 个 `gate1_audio.m4a`（含诊断操作造成的重复）、1 个正常 PDF（标题已被 SDK 改为 `gate1-pdf`），以及 4 个 `gate1_page.pdf` 红色错误孤儿（即此前失败上传留下的壳，网页上显示为不可用）。
- 条件 5 保留项：无会话时 `AUTH_REQUIRED`（真实命令）✔；上传断连时给出 `UPLOAD_FAILED` + orphan id 且不重复上传（真实观察 + 5 个离线测试）✔；上传超时、429/5xx、SIGINT 的系统化注入留 Gate 4。
- Gate 1 结论：**PASS**。后续 Gate 2 起不再需要对 Gate 1 做额外云端上传。


---

## Gate 2 — bili2nb convert 真正可用（本地素材）

- 结果：**PASS（带保留项，见末尾）**
- 日期 / 环境：2026-11-08；macOS 27.0.1 arm64；Python 3.12.15；yt-dlp 2026.10.26；ffmpeg 9.0.2；opencv 5.0.0；代码版本：工作区（未提交）
- 真实外部调用：仅访问公开 B 站（yt-dlp + 公开 view 接口 `api.bilibili.com/x/web-interface/view`），**不涉及 Google 账号、未做任何 NotebookLM 调用**。样本均为公开视频，仅在本机转换用于验收，未再分发。
- 自动化测试：**95 passed / 0 failed / 0 skipped**（`pytest -q`，默认禁止 socket 连接）；`ruff check .` 通过。其中 Gate 2 新增 77 个，覆盖 URL 校验/SSRF、选帧算法（合成视频）、PDF、音频保留/转码/截断/解码、流水线、缓存与原子发布、CLI。

### 真实样本（`bili2nb convert URL --output tmp/gate2/final --json`）

| 项 | 样本 A：短视频（官方 MV） | 样本 B：软件操作演示（多 P 合集 P5） |
|---|---|---|
| URL | `https://www.bilibili.com/video/BV1GJ411x7h7` | `https://www.bilibili.com/video/BV1W64y147w7?p=5`（合集共 30 P） |
| 视频时长（元数据） | 212.4 s | 199.6 s |
| video_key | `BV1GJ411x7h7_137649199`（BV+CID） | `BV1W64y147w7_367119405`（BV+CID，P5） |
| 音频 | `audio.m4a`，AAC，5.41 MB，**直接保留，未转码**（单测证明字节一致） | `audio.m4a`，AAC，3.18 MB，保留 |
| ffprobe 时长 | 212.31 s，误差 0.09 s（容差 2.12 s）**OK** | 199.62 s，误差 0.02 s（容差 2.00 s）**OK** |
| PDF | 9 页（封面+8 图），0.81 MB，书签 9 | 9 页（封面+8 图），1.41 MB，书签 9 |
| 选帧方式 | slides 检测 0 张（连续运动画面）→ 自动补充定时取帧，**给出 warning** | slides 检测，无需回退，无 warning |
| 截图时刻 | 全部在 0–212 s 内 | 64–198 s，全部在时长内 |
| 下载字节 | 51.9 MB（音频 5.4 + 视频 46.5） | 8.8 MB（仅 P5，没下整个合集） |
| 阶段耗时 | metadata 9.2s / audio 6.4s / video 15.9s / frames 1.1s / pdf 0.1s = **32.6 s** | 9.7 / 6.6 / 6.8 / 0.7 / 0.2 = **24.1 s** |

PDF 内容核验：封面有可选中提取的中文标题、作者、原视频 URL、处理日期、时长、截图数量；每页首有 `[HH:MM:SS]` 时间戳，链接为 `…?t=秒`（多 P 为 `?p=5&t=秒`）并可点击；pypdf 独立解析页数/文本/书签通过。渲染页面目检（PyMuPDF 60–75 dpi 预览）：样本 B 的每张截图对应一个操作步骤（资源管理器→PowerPoint 插入音频），界面文字在原生 1280×720 嵌入下可辨认。成品位于 `tmp/gate2/final/<video_key>/`，**请用户用 PDF 阅读器目检**。

### 硬性条件对照

| # | 条件 | 证据 | 结果 |
|---|---|---|---|
| 1 | 公开 URL → 可播放音频 + 非空 PDF，不依赖 Google | 上表；无 Google 调用 | PASS |
| 2 | 先下独立音频流；ffprobe 有音频流且时长误差 ≤ max(2s,1%)；不截断 | 上表；单测 `truncated_audio_is_rejected`（元数据 60s、文件 5s → MEDIA_ERROR） | PASS |
| 3 | 受支持格式保留/remux，不无条件重编码；扩展名与编码一致；符合 200MB | AAC 保留；FLAC 单测转为 AAC 且扩展名 .m4a；超限单测不截断并报错 | PASS |
| 4 | PDF 可打开、可选文本标题/URL、≥1 张清晰截图、时间戳、时刻在时长内 | 上表 | PASS |
| 5 | 多 P 只下指定 P；b23.tv 至少测一个安全重定向 | 样本 B 仅下载 8.8 MB；`https://b23.tv/BV1GJ411x7h7` 真实 302→bilibili.com，转换成功；单测验证跳转到外域被拒 | PASS |
| 6 | ≥2 类样本，记录音频大小/页数/PDF 大小/帧数/分阶段耗时 | 上表（短视频 + 软件演示） | PASS |
| 7 | 错误链接/不可用视频/缺 ffmpeg/解码失败 非 0 + 明确错误码；半成品不当作缓存 | 下表 | PASS |

### 异常路径（真实命令）

| 场景 | 命令要点 | 实际 |
|---|---|---|
| 非 B 站域名 | `convert https://evil.com/video/BV…` | `INVALID_URL`，exit 10 |
| 非单视频链接 | `…/bangumi/play/ep1` | `INVALID_URL`，exit 10 |
| 视频不存在 | `…/video/BV1zzzzzzzzz` | `VIDEO_UNAVAILABLE`，exit 11，信息含 B 站返回码 |
| 分 P 越界 | `BV1W64y147w7 --part 99` | `VIDEO_UNAVAILABLE`“该视频只有 30 个分 P，没有 P99”，exit 11 |
| 缺 ffmpeg | `PATH=/usr/bin:/bin bili2nb convert …` | `TOOL_MISSING`，exit 13，提示 `brew install ffmpeg`；**下载前即失败** |
| 解码失败/截断/超限/磁盘不足 | 单元测试（注入） | `MEDIA_ERROR` / `DISK_FULL`；失败后无半成品目录、临时目录已清理（真实运行后 `work/` 为空） |

发现并修复的缺陷（均由测试/真实运行发现）：
1. 回退定时取帧在视频短于 15 秒时返回空列表，会被误判为“无画面”——已改为按时长均分且至少 1 帧（有回归测试）。
2. yt-dlp 对不存在视频/分 P 越界只给无意义报错（`KeyError('bvid')` / `No video formats found`）——改为先用 view 接口预检，给出明确原因；风控返回码（-412 等）不会被误判为“视频不存在”。
3. yt-dlp 不暴露 CID——改从 view 接口取 CID 构造 `BV+CID` 的 video_key，失败时降级为 `BV_p<N>` 并给 warning。
4. 多 P 标题重复拼接分 P 名——已去重。

### 保留项与已知限制（诚实记录）
- 尚未测试：30–60 分钟长视频、课件讲座（PPT）类样本、关键页召回率/重复率量化 → 属 **Gate 4**（本关卡要求“短视频、课件讲座或软件演示其中两类”，已满足：短视频 + 软件演示）。
- b23.tv 仅测了 `b23.tv/BV…` 形式的真实跳转；形如 `b23.tv/xxxxxx` 的分享短码走同一代码路径（逐跳校验主机），但未用真实分享码实测。
- 样本 A（MV）无幻灯片，依赖定时取帧兜底，截图仅为均匀采样，不代表“重要画面”判断。
- 真实 SIGINT 中断、B 站限速 412 的真实触发留 Gate 4（目前仅单元测试覆盖）。
- opencv-python 与 opencv-python-headless 并存（均 5.0.0.93），当前运行正常，未处理。


---

## Gate 3 — bili2nb import 端到端自动入库

- 结果：**PASS（自动化部分；真实端到端已完成，见本节末“Gate 3 最终真实运行记录”）**。本节中间的 BLOCKED 记录是历史过程，保留作证据；待办的网页确认归入 Gate 5。
- 日期 / 环境：2026-11-08；macOS 27.0.1 arm64；Python 3.12.15
- 自动化测试：**130 passed / 0 failed / 0 skipped**（`pytest -q`，断网，NotebookLM 为内存假实现）；`ruff check .` 通过。⚠️ 这些仅证明编排逻辑，**不代表真实 NotebookLM 通过**。

### 已实现
`bili2nb import / status / retry / jobs`；SQLite 任务库（`~/Library/Application Support/Bili2NotebookLM/jobs.sqlite`，权限 0600，只存 id/路径/状态，无凭证）；阶段检查点 `created → converted → notebook_created → uploaded → ready → done`，notebook_id 与每个 source_id 在返回后**立即落库**。

### 离线逻辑测试覆盖（对应 Gate 3 条件）
| 条件 | 测试证据 |
|---|---|
| 2 下载前预检登录 | `test_auth_preflight_happens_before_any_download`：未登录时 fetch/convert/建本调用数均为 0，且不创建任务；变异测试（把预检挪到取元数据之后）会使该测试失败 |
| 2/3 两条来源各自 source_id 持久化；READY 才完成；audio-only 明确标注 | `test_import_creates_notebook_and_two_ready_sources`、`test_audio_only_has_one_source_and_says_so`、`test_visual_skipped_yields_audio_only…` |
| 4 中途失败/Ctrl+C/超时可 status+retry；已成功来源不重传、复用原 notebook | `test_pdf_upload_failure_then_retry_does_not_duplicate_anything`（音频只上传 1 次、建本 1 次、最终恰好 2 个来源）、`test_ctrl_c_marks_interrupted_and_retry_resumes`、`test_not_ready_timeout_keeps_ids_and_retry_only_waits`、`test_source_rejected_by_notebooklm_is_reuploaded_on_retry`、`test_files_lost_between_failure_and_retry_are_regenerated`；变异测试（取消“已上传则跳过”）使其中 5 项失败 |
| 5 同视频/同分 P/同账号再次 import 不重复建本；仅 `--force-new` 重建 | `test_second_import_of_same_video_reuses_job_and_notebook`、`test_force_new_creates_another_notebook`、`test_different_mode_or_account_is_a_different_job`、`test_deleted_notebook_makes_new_job…`、`test_rerun_import_resumes_unfinished_job…` |
| 6 `--json` stdout 恰为一个 JSON 对象、进度走 stderr、无凭证 | `test_cli_import_json_success_single_object`、`test_cli_import_failure_json_has_job_and_codes`、`test_job_dict_has_no_secret_fields` |
| 7 所有已公开命令有测试；不支持选项不在 `--help` | `test_cli_status_retry_jobs_commands`、`test_cli_help_shows_only_implemented_options`（`--no-wait` 未公开）；auth check / convert 见 Gate 1/2 测试 |
| 限次退避（PRD §3.5） | `test_rate_limit_backs_off_then_succeeds`（等待 5s、15s）、`test_persistent_rate_limit_stops_after_limit…`（3 次后失败、保留可恢复 Job，无无限循环）、`test_ambiguous_upload_failure_is_not_blindly_retried` |
| 防重复建本（Gate 1 教训） | `test_create_notebook_lost_response_but_created_is_found`（挑选新建的而非同名旧笔记本）、`…nothing_created_not_auto_retried`（歧义建本失败不自动重试） |
| 非交互安全（PRD §2） | `test_cli_auth_required_is_non_interactive_in_json_mode`、`…never_prompts_when_not_a_tty`：`--json`/非 TTY 绝不弹登录交互 |

### 尚未完成（需要用户授权，故 BLOCKED）
1. 条件 1–3：真实 B 站 URL → NotebookLM 新笔记本，两个来源 READY，notebook_url 可打开并看到来源。
2. 条件 5 真实版：同链接二次运行返回原笔记本、不新建。
3. 条件 4 真实版：真实 Ctrl+C / 失败后的 `status` / `retry`（逻辑已由离线测试覆盖）。


### Gate 3 真实运行记录（用户已同意：用 `BV1W64y147w7?p=5` 新建 1 个正式笔记本）

| # | 动作 | 实际结果 |
|---|---|---|
| 1 | `bili2nb auth check` | OK（24 本） |
| 2 | `bili2nb import "…BV1W64y147w7?p=5" --json` | **FAIL**，exit 22 `UPLOAD_FAILED`（原始异常 `httpx.RequestError`，被当作“未知错误”）。转换成功（音频 3.2 MB、PDF 9 页/1.4 MB，15 s）；笔记本已建；**任务已持久化**（job `20261108-175358-200f`，stage=notebook_created，0 个来源） |
| 3 | 云端核查（只读） | 账号笔记本数 24→**26**：出现**两本同名笔记本**，创建时间只差 2 秒（`NB-1` 被任务记录；`NB-D1` 为**重复的空笔记本**） |
| 4 | 定位 | SDK 内置的服务端错误自动重试（`server_error_max_retries=3`）在第一次“创建笔记本”响应丢失后**又发了一次非幂等的创建请求**。当时的对账逻辑只包住了 SDK 之外的调用，拦不住 SDK 内部重试 |
| 5 | 修复（先写失败测试再修） | ① `create_notebook` 用 `server_error_max_retries=0` 的客户端，重试交给本项目自己的对账；② `httpx.RequestError`（curl_cffi 抛出的原始网络错误）纳入网络类错误，创建/上传都会先对账再决定是否重试，并映射为可退避的 `NETWORK_ERROR`。新增 4 个回归测试（修复前 4 个全部失败），全套 **134 passed**，ruff 通过 |
| 6 | `bili2nb retry 20261108-175358-200f` | **FAIL**，exit 25 `NETWORK_ERROR`：预检/查笔记本连续网络错误，按设计退避 5 s、15 s 后停止，任务保持可恢复，未新建任何笔记本 |
| 7 | 网络诊断（只读，未改任何网络设置） | 同一时段：SDK 列表请求 curl_cffi 8 次失败 6 次（`curl: (56) Connection closed abruptly`）、httpx 8 次失败 6 次（`incomplete chunked read`）；**纯 `curl https://www.google.com/` 10 次失败 7 次（`curl: (18) Transferred a partial file`）**。结论：本机到 Google 的网络路径在传输中途截断响应（本机无系统代理，但存在多个 utun 虚拟网卡），属环境问题，非本项目代码。Gate 1 当时的“服务端断开”很可能同源 |

**云端遗留（均未删除，待用户决定）**：重复的空笔记本 `NB-D1`；目标笔记本 `NB-1`（目前 0 个来源，等待 retry 填充）；Gate 1 测试笔记本及其孤儿来源。

**Gate 3 状态：仍 BLOCKED**——真实端到端未完成（两个来源 READY、二次运行不重复、真实恢复），原因是上述网络不稳定；但这次真实运行**发现并修复了一个会重复建本的严重缺陷**（验收一票否决项的同类风险）。

**后续重试记录（同一任务 `20261108-175358-200f`，均在用户同意范围内）**

| # | 动作 | 结果 |
|---|---|---|
| 8 | 后台探测（`curl google.com` 6/6 成功）后自动 `retry` | FAIL exit 25：查笔记本 2 次、上传音频 3 次均 `NETWORK_ERROR`（虽然同一时刻纯 curl 正常，说明 NotebookLM 的 POST 请求被截断的概率高于普通下载） |
| 9 | 加固：幂等操作退避上限 3→5 次（3/9/27/81 s）；上传网络中断且云端确认未收到文件时标记为可重试 `NETWORK_ERROR`，仅发现孤儿来源时才用 `UPLOAD_FAILED`。135 passed | — |
| 10 | 再次 `retry` | FAIL exit 22：已走到上传阶段，音频**注册成功但字节上传被断开**，留下孤儿来源（PREPARING）。**同一时段账号笔记本总数保持 26，没有再新建笔记本**（重复建本修复有效） |
| 11 | 诊断：纯 curl 大 POST（20 KB–3 MB 到 google.com）6/6 基本都能发完 | 排除“请求体过大被切”；失败呈**按连接随机**发生，约三到五成连接被中途切断 |

目标笔记本当前状态（只读核查）：2 个孤儿音频来源（PREPARING），0 个 READY 来源；同名笔记本共 2 本（含上文重复的空本）。每次上传在“注册之后”失败都会多留一个孤儿来源。

**Gate 3 仍 BLOCKED。** 待决策：是否授权工具自动清理“本任务自己创建的、卡在 PREPARING/ERROR 的孤儿来源”，以便在不稳定网络下安全地反复重试（当前不会删除任何云端内容）。

### Gate 3 补充记录（19:20，FDM 已停止下载后重试）
- 用户确认 FDM 下载结束（脚本实测 fdm 入站速率 0 KB/s）后，对同一任务执行 `bili2nb retry 20261108-175358-200f`。
- 结果：仍 `UPLOAD_FAILED`（音频注册成功后，字节上传被服务端断开）。**带宽被占用不是唯一原因**。
- 云端核对（只读）：笔记本 `NB-1` 现有 4 个 `audio.m4a` 孤儿来源（均 PREPARING），0 个 READY；账号笔记本总数未增加。
- 结论：Gate 3 仍为 **BLOCKED**，未做任何云端删除。

### Gate 3 补充记录（授权清理孤儿后重试）
- 用户授权“只清理本工具自己留下的、卡在 PREPARING/ERROR 的来源”。实现：`upload_file` 上传前清理同名卡住来源，失败后删除本次孤儿并改为可重试的 `NETWORK_ERROR`；新增 5 项测试（共 140 项通过，ruff 通过）。只删 PREPARING/ERROR 且文件名匹配者，READY/PROCESSING 与他人来源不动。
- 已清理笔记本 `NB-1` 中 4 个 `audio.m4a` PREPARING 孤儿（真实删除，核对后笔记本来源数 0）。
- 随后 `retry`：5 次自动退避（3/9/27/81s）上传音频仍均被服务端断开，每次孤儿都被自动清理；最终 `NETWORK_ERROR`（可安全重试），无孤儿残留，笔记本总数未增加。
- 结论：清理机制验证有效；上传本身仍受本机到 Google 的连接不稳影响，Gate 3 仍为 **BLOCKED**。

### Gate 3 最终真实运行记录（2026-11-08 22:3x，`BV1W64y147w7?p=5`）
**根因诊断（实测）**：上传“完成上传”一步约 8 秒后连接被对端断开（`curl: (56) Connection closed abruptly`）。同一音频剪出不同大小对比：≤76 KB 正常；约 96 KB–2 MB 客户端报错但服务端已收下并变 READY（约 30 秒）；3.2 MB 当次未收下。每次尝试还会多出一个卡住的 PREPARING 孪生来源。结论：客户端“报错”不等于失败，必须在足够长的窗口内核对云端再决定。
**修复**：核对窗口 30s→90s；`_reconcile` 优先采用 READY/PROCESSING 的来源（不被卡住的孪生挡住）；仅在窗口结束仍未可用时才清理本次孤儿。新增回归测试，共 141 项通过，ruff 通过。
**结果**：
- `retry 20261108-175358-200f`：音频经 4 次退避后（共 attempts=7）上传成功，PDF 复用此前已被服务端收下的来源；两个来源均 **READY**；任务 `completed`，输出 NotebookLM URL；成功后本地媒体已清理（files_cleaned=true）。
- 幂等：再次 `import` 同一链接，返回 `reused: true`、原笔记本 URL、source_count=2；账号笔记本总数前后均为 26（未新建）。
- 已清理本工具遗留的卡住来源（PREPARING，同名）共 5 个，未触碰 READY 来源。
**Gate 3 状态：PASS（自动化部分，真实证据如上）；待办**：① 用户在网页确认笔记本内含 2 个来源且问答可用；② 笔记本中另有 2 个诊断时上传的 `gate1_audio.m4a` 测试来源（READY），超出已授权的删除范围，待用户同意后删除；③ 第一次失败遗留的空重复笔记本 `NB-D1` 仍待用户决定。
- 用户已同意后，删除了任务笔记本 `NB-1` 中诊断时上传的 2 个 `gate1_audio.m4a` 测试来源（READY）。核对：笔记本现仅含 `audio.m4a`（READY）与 `keyframes.pdf`（READY）共 2 个来源。仍待：用户网页确认；空重复笔记本 `NB-D1` 待用户决定。

## Gate 4 — 可靠性、视觉效果及性能回归（2026-11-08/09，结论见本节末）

结果：**PASS**（过程中的“进行中/待验证”表述是历史记录，最终结论见“Gate 4 结论”）。

### 1. 三类真实样本 benchmark（convert，仅本地，不触碰 Google；公开视频，仅本机测试，素材未分发）
| 类型 | 视频 | 时长 | 总耗时 | 峰值内存(RSS) | 音频 | PDF | 截图数 | warning |
|---|---|---|---|---|---|---|---|---|
| 屏幕教程（Excel 演示） | BV1VV4y1V7Pz | 6.3 min | 22.9 s | 346 MB | 6.1 MB | 2.2 MB | 16→17 | 无 |
| 普通口播（播音课，含叠加文字页） | BV1M34y1B71N | 19.8 min | 102 s（视频下载 63 s） | 468 MB | 47.4 MB | 10.0 MB | 42 | “画面变化很少…已补充定时取帧”（如实提示，非静默） |
| **PPT 讲座（长，38 min）** | BV1QW4y1e7iC?p=2 | 38.1 min | 51.5 s → 41.9 s | 462 MB | 37.2 MB | 2.9 MB → 2.3 MB | 17 → 14 | 补充定时取帧（如实提示） |
（耗时取 `/usr/bin/time -l`；CPU user 时间 9–55 s；原始数据：`tmp/gate4/*.json/*.err`。）

### 2. PPT 样本召回/重复（人工参照 + 仅作对照的程序统计）
- 参照清单建立方法：对该 38 分钟视频每 4 秒密采样，按画面主体变化>3%分出独立页，**人工逐张目视核对联系表**，确认共 **14 个独立页面**（标题页、目录、章节页、陆九渊页、阳明心学页…谢谢观赏；联系表：`tmp/gate4/ref/lecture_states.jpg`）。局限：4 秒采样可能漏掉短于 4 秒的页；参照由人工核对，可复核（联系表见开发记录）。
- **发现的缺陷**：该视频带烧录字幕，同一页因字幕变化被当成多页——修复前 17 页：召回 13/14=93%，**重复 4 页=24%（超过 ≤10% 目标）**。
- **修复**：比较帧时忽略底部 14% 字幕带（保存的截图不裁剪）；先写 4 项失败测试再改代码。
- **修复后 14 页：召回 14/14=100%，重复 0 页=0%**（均与参照逐页对应，差异 0.000）。目标 召回≥90% / 重复≤10%：**达成（仅限此测试样本，不外推）**。
- 清晰度：1280×720 原帧写入 PDF，PPT 正文、小字号引文肉眼可读（人工查看联系表）。PDF 2.3 MB（口播 10 MB），未为压缩牺牲可读性。

### 3. 回退与 warning（条件 4）
SceneDetect 失败 → 回退定时取帧并写 warning（单元测试覆盖）；幻灯片检测过少 → 补充定时取帧并写 warning（真实样本已触发并如实显示）；全黑/纯色才会判定“无视觉内容”。

### 4. 故障注入（条件 5）——真实运行 + 单元测试
| 场景 | 真实运行结果 | 单元测试 |
|---|---|---|
| ffmpeg 缺失（PATH 去掉 Homebrew） | `TOOL_MISSING` 退出码 13，下载前即失败 | ✓ |
| Google 未登录（不存在的 profile） | `import` → `AUTH_REQUIRED` 退出码 20，**未下载**（work 目录 0 条）；同环境 `convert` 仍成功 | ✓ |
| SIGINT（下载阶段 Ctrl-C） | 退出码 **130**，`INTERRUPTED`，无半成品、无 work 残留；重跑 convert 成功 | ✓ |
| 无效链接 / 不存在视频 | `INVALID_URL`(10) / `VIDEO_UNAVAILABLE`(11，含明确原因) | ✓ |
| B站 403/412/429 限速 | —（未能真实触发） | ✓ 分类为 `RATE_LIMITED`/`VIDEO_UNAVAILABLE` |
| NotebookLM 429/5xx、上传卡死（超时）、持续失败 | —（真实 Gate 3 已遇大量网络类失败：最多 5 次指数退避后保留任务，retry 恢复且不重复建本） | ✓ 新增矩阵测试：ServerError/TimeoutError/RateLimitError 映射为可重试码；持续失败**恰好 5 次**后停止、任务可恢复、重试不重复建本；不可重试错误不退避 |
| 磁盘不足 | —（未真实触发） | ✓ 下载前检测 |
注：SIGINT 真实测试需用子进程发送信号（shell 后台任务会忽略 SIGINT，第一次尝试因此无效，已改用 Python subprocess）。

### 5. 自动化与干净环境复现（条件 6、7）
- `pytest`：**151 passed**（默认离线，socket 被禁）；`ruff check`：All checks passed。
- 干净环境复现：把项目拷到全新临时目录（不含 .venv/tmp/.git），`uv sync` → `uv run bili2nb --help` → `uv run pytest`（151 passed）→ `ruff` → README 示例 `convert`（真实下载，status ok）→ `auth check`（未登录返回 AUTH_REQUIRED/20）全部成功。
- 未引入 Whisper/VLM/OCR/Agent 依赖/后台服务（依赖清单见 ENVIRONMENT.md）。
- README 已完成：安装、登录、convert/import、失败恢复、登录失效修复、退出码、已知限制。

### 6. 尚未完成（需要用户参与）
- **长视频真实 import**：上述 38 分钟讲座音频 37 MB。Gate 3 发现“约 96 KB 以上上传最后一步会被断开，服务端对 ≤2 MB 的文件仍会收下，3.2 MB 需多次重试才成功”。37 MB 很可能触及同一问题，需真实验证（将新建 1 个笔记本，不删除任何内容）。**若失败，需实现分块断点续传或其他方案，否则 Gate 4 不能 PASS。**

### Gate 4 补充：长视频真实导入（用户同意，2026-11-09）与两个根因
**样本**：BV1QW4y1e7iC?p=2（PPT 讲座 38 分钟，音频 37.2 MB，PDF 2.3 MB/14 张）。
**第一次 `import`（修复前）失败**：音频上传 5 次均 `NETWORK_ERROR`（3.2 MB 以上的单请求上传会被断开），每次失败遗留的卡住来源已被自动清理。
**根因 1：大文件单请求上传在本链路上约 8 秒被断开** → 新增 `notebook/chunked_upload.py`：按 Google 断点续传协议每 2 MB 一块上传（`upload` + offset，最后一块 `upload, finalize`），每块失败先 `query` 服务端已收字节再从服务端偏移量续传，每块最多 4 次、有界。10 项离线逻辑测试（用模拟服务器注入“回执丢失/未收到/最终化丢失/持续失败/不可重试”）。
**真实验证**：测试笔记本中 37 MB 音频一次成功（68 s，约 140 s 后 READY）；随后 `retry` 任务：音频+PDF 上传共 135.8 s、**首轮零网络错误**、READY 用 39.7 s，任务 `completed`，两个来源均 READY，本地文件已清理。笔记本 `NB-2` 含 `audio.m4a`（READY）与关键帧 PDF（READY）。
**根因 2（严重，Gate 4 否决项类）：重复建本**。本次导入又出现两个同名笔记本（创建时间相差 2 秒；Gate 3 首次也是同样模式）。SDK 自身已声明创建“只发一次”且禁用内部重试，所以问题在更底层：用本地探针 `scripts/probe_curl_post_retry.py` 复现——**libcurl 在“被复用的长连接无响应断开”时会静默重发 POST**（本机服务端收到 2 次 POST；加 `FRESH_CONNECT`+`FORBID_REUSE` 后为 1 次）。Google 常常处理完请求后才断开连接，于是创建被执行两次；同一机制也解释了每次上传都多出一个卡住的“孪生”来源。
**修复**：`NotebookAdapter` 对非幂等写（创建笔记本、上传）强制每个请求使用新连接（`_fresh_connections`），只读/幂等调用不受影响；上传成功后顺带清理同名卡住孪生。单元测试 164 项通过，ruff 通过。
**⚠ 未完成的真实验证**：根因 2 的修复**尚未在真实“创建笔记本”上验证**（本次长视频笔记本是修复前创建的）。账号笔记本现 28 个：其中 `NB-D1`（Gate 3）与 `NB-D2`（本次）是修复前产生的**空重复笔记本**，均未删除，待用户决定。
**Gate 4 当前判定：尚不能标 PASS**——硬性条件 1–7 的本地与真实证据已具备，但“严重重复建本”是 Gate 4 的 FAIL 条件，修复需用一次真实导入确认只产生 1 个笔记本后，才能通过。

### Gate 4 修复后真实验证（用户同意，2026-11-09）
样本：BV1VV4y1V7Pz（Excel 屏幕教程，6.3 分钟，音频 6.1 MB，PDF 2.3 MB/17 张）。修复后的代码，`import` 一次：
- 账号笔记本数 **28 → 29**，同名笔记本 **恰好 1 个**（`NB-3`），**无重复**；
- 两个来源 `audio.m4a`、关键帧 PDF 均 **READY**，且**没有卡住的孪生来源**（修复前每次上传都会多出一个）；
- `attempts=1`，零网络错误/零重试；用时：转换 13.3 s、建本 6.6 s、上传 56.9 s、READY 8.2 s；
- 再次 `import` 同一链接：`reused: true`，返回原笔记本 URL，笔记本总数仍为 29。
局限：仅 1 次真实创建；修复前 2/2 次导入出现重复，修复后 1/1 次无重复，样本量小，Gate 5 的第二条视频会再次观察。账号中仍有 2 个修复前遗留的空重复笔记本（`NB-D1`、`NB-D2`），未删除，待用户决定。

## Gate 4 结论：**PASS**
- 条件 1：三类真实样本（含 38 分钟长视频）完成 benchmark，并**完成长视频与短视频的真实 import**（两者来源均 READY）。
- 条件 2：PPT 样本召回 14/14=100%，重复 0%（修复字幕重复缺陷后；修复前 24%）。
- 条件 3：截图 1280×720，文字清晰，PDF 2.3–10 MB。
- 条件 4：回退并带 warning（单测 + 真实样本触发）。
- 条件 5：故障注入真实演练（ffmpeg 缺失/未登录/SIGINT/无效链接/视频不存在）+ 单元矩阵（5xx/超时/429/持续失败有界且可恢复）。
- 条件 6：pytest **164 passed**，ruff 通过；干净环境按 README 复现通过（本节之前的记录）。
- 条件 7：无 Whisper/VLM/OCR/Agent/后台服务。
- FAIL 条件核对：无数据丢失；无明文凭证泄漏（任务库只存 id 与路径）；**重复建本**在测试中出现过 2 次，已定位根因（libcurl 静默重发 POST）、修复，并经真实创建验证；无不可恢复的网络中断（长视频上传经分块续传成功）。


## Gate 5 — 最终验收（2026-11-09；标准于 V1.1 修订）

**结果：PASS**。V1.1 起验收标准为：CLI 自动完成导入（无人工上传/转录）、两个来源均 READY、NotebookLM **网页版**可对视频语音和至少一个视觉信息点正常问答。手机端 NotebookLM 同步由 Google 负责，不再是必验项（使用者曾自愿在手机上确认过笔记本与来源可见，未计时、不计入判定）。

### 对照 ACCEPTANCE Gate 5 的各项条件
| 条件 | 结果 | 证据 |
|---|---|---|
| 1 首次授权一次后执行 `import` | PASS | Gate 3/4 真实导入（3 条不同视频） |
| 2 无人工步骤；URL 可打开；两来源 READY；网页版语音 + 视觉问答准确 | PASS | 两个笔记本各 8 题（画面 / 语音 / 画面+语音 / 跨来源 / 反例），共 16 题全部通过；题目见 `GATE5_QA.md` |
| 3 手机端可见 | 已取消（V1.1） | — |
| 4 第二条不同视频成功；重启终端后无需重新登录 | PASS | 3 条不同视频真实导入，每次均为新进程/新命令，未重复登录 |
| 5 无认证时 `convert` 成功、`import` 报 `AUTH_REQUIRED` | PASS（用不存在的 profile 模拟；未真实清除登录状态） | `convert` 成功；`import` / `auth check` 退出码 20，下载前即失败 |
| 6 README 示例可复现 | PASS | 干净临时目录 `uv sync` → help → pytest → ruff → convert → auth check；V1.1 起另有 CI 的全局命令冒烟测试 |
| 7 不擅自删除云端内容；Cookie 不进仓库/日志/导出包 | PASS | 云端删除仅在使用者明确同意后进行；凭证扫描（项目文件、日志、文档、任务库、Git 历史）未发现 Cookie 或令牌；任务库无敏感列 |

### 发现并修复：PDF 封面在 NotebookLM 预览中乱码
- 现象：每个 PDF 的第一页（封面，唯一含中文文字的页）在 NotebookLM 来源预览里显示乱码；本地用桌面阅读器查看正常。截图页（图片）不受影响。
- 根因：封面使用 ReportLab 内置 `STSong-Light`——**未嵌入的 CID 字体**。桌面阅读器用本机字体替代所以正常，NotebookLM 的预览/文本提取没有该字体，输出乱码。此前只用 pypdf 验证“文字可提取”，该检查发现不了字体未嵌入（验证盲区）。
- 修复：改为**嵌入 TrueType 字体子集**（macOS：`STHeiti Light` / `Arial Unicode`；Windows：`simhei` / `msyh` / `simsun`；Linux：`wqy-microhei` / `DroidSansFallbackFull`；可用 `BILI2NB_PDF_FONT` 指定）；找不到可嵌入字体时回退旧字体，并在 `convert` / `import` 的 warnings 与 `doctor` 中明确提示。先写失败测试再实现。
- 验证：重新生成的 PDF 封面字体已嵌入且带 ToUnicode，文字提取与渲染均正确；已用新 PDF 替换两个已导入笔记本里的旧 PDF 来源（先上传新 PDF 并等待 READY，再仅删除已知的旧 PDF 来源 ID），使用者确认预览显示正常。
- 云端清理（使用者同意）：删除了 2 个空的重复笔记本（NB-D1、NB-D2，删前核对均为 0 个来源）和 Gate 1 的测试笔记本 NB-T（23 个测试来源）。

## 体验改进：yt-dlp 式使用（2026-11-09）
- 需求：`bili2nb <链接或BV号>` 直接导入；首次使用设置工作目录与 Google 登录；各系统有默认工作目录且可手动修改。
- 实现：第一个参数不是子命令即视为 `import`（选项可放前面）；裸 BV/av 号规范化后仍走原严格 URL 校验；`setup` 命令（交互或 `--work-dir/--no-login/--json`）；首次在终端运行自动引导，非终端从不提问；工作目录优先级 `BILI2NB_HOME` > 已保存设置 > 平台默认；配置文件只存路径，不存凭证；ffmpeg 安装提示按平台区分；命令名为 `bili2nb`（短）与 `bili2notebooklm`（与项目名一致）。
- 验证：先写失败测试再实现；用临时 `uv tool` 目录在项目外运行 `--help`、`<BV号>`（已导入视频 → `reused: true`，未新建笔记本）、无效输入 → `INVALID_URL`（退出码 10）、非交互 `setup`。
- **事故与修复**：验证时 `setup` 在 `BILI2NB_HOME` 沙盒下仍把配置写进真实的默认目录（会让任务库“消失”）。发现后立即删除该 `config.json`（任务库未受影响），并修复为：设置了 `BILI2NB_HOME` 时配置文件放在该目录内；新增回归测试。

## 跨平台自动测试（GitHub Actions，2026-11-09）
- 每次推送在 macOS / Windows / Linux 各运行：安装依赖 → Ruff → 离线 Pytest → `doctor`；另有“安装成全局命令”冒烟测试（`uv tool install . --overrides overrides.txt` 后在项目目录外运行）。V1.1 对 CI 做了加固，详见 V1.1_HARDENING.md。
- CI 实际抓到并促成修复的 Windows 问题：① 离线测试守卫禁止了所有 `connect()`，而 Windows 的 asyncio 用回环 `connect()` 建事件循环，导致所有异步测试失败（现放行回环地址）；② Typer 在 GitHub Actions 强制带颜色码，拆开了帮助文字里的选项名；③ ffmpeg 安装提示、文件权限（0600）的断言是 macOS 专属。
- Windows CI 上 `bili2nb doctor` 实测：Python、ffmpeg、yt-dlp、opencv、notebooklm-py、中文字体（可嵌入）、工作目录（`%APPDATA%\Bili2NotebookLM`）均 OK；Google 登录为 WARN（CI 没有账号，符合预期）。
- **未覆盖**：Windows / Linux 上的真实 Google 浏览器登录、真实 NotebookLM 导入、真实 B 站下载。
