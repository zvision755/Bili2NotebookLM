# Bili2NotebookLM — V1 产品需求与技术实现规范

> 状态：开发需求定稿（2026-11-08）
> 目标宿主：Mac（Apple Silicon，macOS），由 Claude Code 开发
> 项目目录：~/Projects/Bili2NotebookLM
> 文档适用范围：仅 V1 本地 CLI，不含 QQ Bot、Hermes、DSH、Cloudflare Tunnel、Web UI、Obsidian 插件

## 0. 最高优先级目标

用户复制一个可访问的哔哩哔哩视频 URL，在 Mac 终端执行一条命令后，无须手动下载、转录、生成笔记或浏览器上传，即可在同一个 Google 账号的 NotebookLM 中看到新建的笔记本，包含：
1. 原视频音频来源：由 NotebookLM 自己完成音频转录与语义索引，本地不运行 Whisper。
2. 智能提取的视频视觉内容来源：一份含代表性截图、时间戳与原视频链接的精简 PDF，供 NotebookLM 阅读视觉信息。
3. 笔记本标题与原视频标题对应，命令执行结束打印真实 NotebookLM URL。

“几分钟”是用户期望的交互体验，不是可保证的 SLA；耗时受视频长度、B 站网络和 NotebookLM 处理队列影响。须打印实际分阶段耗时、清晰状态和最终结果，禁止假报成功。

关键成功定义：两份来源均上传并被 NotebookLM 报告为可用（ready / equivalent），CLI 才返回 exit code 0 与 notebook_url；若视频确实没有可提取的有意义画面或用户明确指定 audio-only，允许只有音频来源，但必须显式标注。必须在手机 NotebookLM 使用相同账号进行一次人工端到端验证；手机刷新速度不能由 CLI 保证。

## 1. 非目标与开发边界（V1 坚决不做）

- 不开发 QQ Bot、Hermes、DSH-IM、Cloudflare Tunnel、公网 API、后台常驻服务、Web GUI、Chrome 插件、Obsidian 插件。
- 不开发本地 Whisper/ASR、模型总结、RAG、向量数据库、VLM、OCR 工作流；NotebookLM 承担转录、总结、对话、学习资料生成。
- 不自行逆向 Google 的私有协议；优先复用 notebooklm-py 的公开 Python SDK / CLI。它是非官方接口，必须封装隔离，不承诺永不失效。
- 不实现绕过 DRM、付费权限、验证码或平台访问控制。只处理用户有权访问与处理的视频。
- 不依赖 ChatGPT/Codex/Claude 订阅额度参与常规运行；Claude Code 只用来开发。
- 不追求一次处理整套合集/无限批量队列；首版一条 URL 对应一个明确分 P 视频。未来需要再扩展。
- 不自动发布、分享、删除 NotebookLM 账号里的现有资源；默认操作仅创建与新增。
- 不未经用户确认读取/复制浏览器中的登录 Cookie；不要求用户把 Cookie 粘贴到聊天、日志、Git 仓库中。

## 2. 交互与命令契约（以实测 SDK 支持能力为准，保持 CLI 兼容）

可执行命令名：bili2nb。主模式：
- bili2nb import URL            # 默认全自动：转换 + 新建 NotebookLM + 上传 + 等待可用
- bili2nb convert URL           # 仅转换素材，不连接 Google；产出音频 + 可选 PDF
- bili2nb auth login            # 第一次人工浏览器登录 / 保存本机 NotebookLM 会话
- bili2nb auth check            # 校验当前认证是否真的可调用 NotebookLM
- bili2nb auth refresh          # 如果 SDK 支持则安全刷新，不支持要清晰提示重新登录
- bili2nb status JOB_ID         # 查看持久化任务结果
- bili2nb retry JOB_ID          # 从已记录阶段恢复，不能盲目重复创建笔记本
- bili2nb jobs                  # 查看本地执行历史

主命令选项（首版最低要求）：
- --frames auto|slides|scene|none （默认 auto）
- --output DIR （convert 输出路径、import 缓存可选保留路径）
- --json （stdout 仅一个稳定 JSON 结果；日志和进度走 stderr）
- --keep-files （import 成功后保留导入文件）
- --force-new （明确指定允许为同一视频创建另一本笔记）
- --part N （多 P URL 的显式分集选择，若 URL 已明确分 P 则尊重其指定）
- --audio-only （等同 frames none，明确允许仅有音频来源）
- --no-wait （可选后续优化；首版默认等待 NotebookLM 来源 ready）

不要为了预留接口而将全部选项做成无效占位符。每个支持的选项必须有测试，尚未做的应从 --help 隐藏。

首次使用没有有效认证时：
- convert 不受影响。
- import 应尽早给出 AUTH_REQUIRED + 明确的 auth login 操作指示；交互模式可以询问是否引导登录，非交互 / --json 模式绝不能弹出不可预期的交互。
- 不要求每次手动复制 Cookie；使用 SDK 的受支持的浏览器登录和持久会话方式。Cookie、CSRF token、Master Token 视为高敏感凭证。

成功输出应明确包含 job_id、video_id、video_title、notebook_id、notebook_url、source_ids、audio_path/pdf_path（存在才返回）、status、duration_sec、warnings。
失败使用非 0 exit code，稳定错误码（例如 AUTH_REQUIRED、VIDEO_UNAVAILABLE、MEDIA_ERROR、NOTEBOOK_CREATE_FAILED、UPLOAD_FAILED、SOURCE_NOT_READY、RATE_LIMITED、INTERRUPTED）；JSON 不输出任何敏感认证信息。

## 3. 工作流与处理策略

### 3.1 链接解析与视频元数据
- 支持 https://www.bilibili.com/video/BV... 与 https://b23.tv/...（短链需安全解析），如允许 av 号可加入；用户传入的 URL 必须做协议/主机名白名单，避免 SSRF。
- 通过 yt-dlp 提取 title、BV/aid、CID/part、时长、发布者、原始地址、音视频格式，不要无意下载整个合集。
- 标准化为唯一的 video_key（BV + CID 或等价独立分 P 标识），并为目录名进行跨平台非法字符过滤、长度裁剪。
- 对需要访问权限的内容，B 站登录态应与 Google Cookie 严格分离；缺少凭证时给出有用错误，不绕过限制。
- 默认只处理一段视频；多 P、长视频、失败链接应有可预期行为。

### 3.2 音频处理：优先无损且避免整段视频下载
- 首选 yt-dlp 只选择独立音频流，不为获取音频先下载整个视频。
- 如原始编码和容器符合 NotebookLM 接受类型，直接保留或无损 remux；不为“压缩”无条件重编码。
- 仅当格式不兼容、单文件体积超过上传限制、或用户明确选择才通过 FFmpeg 转码，优先保语音识别质量与正常语速；不能超速拉伸声音。
- 输出真实扩展名与实际媒体编码匹配；调用 ffprobe 验证时长、大小、流类型与可解码性。
- 以官方当前约束为准并在开发 P0 重新核验 NotebookLM 接受的扩展名、文件/来源限制；不要把来自旧版本的容量/格式猜测写死为永远正确。
- 不截断长音频；确需分割时首版报明确限制，不悄悄切掉后半段。

### 3.3 视觉处理：真正的“内容关键帧”，不是编码 I-frame
- 只有为 PDF 才下载视频画面；低分辨率或抽样解码作粗筛，再在能力允许时获取足够清晰的代表帧。
- 对 PPT、教学讲座：选取内容稳定后的幻灯片，过滤黑屏/过渡帧，处理渐进动画（优先保留信息最完整的阶段或独立新增知识）。
- 对软件教程/实验视频：兼容持续变化画面；结合场景检测与固定时间抽样兜底，避免只识别强镜头切换。
- 推荐基线：PySceneDetect + OpenCV 的缩略帧/灰度差异 + pHash/SSIM 去重；阈值可配置，但不能据传闻就宣称能自动识别所有重点。
- B站预览雪碧图只可做低成本候选时间点参考；若它分辨率太低、不可用、抽样太稀疏，应回退到视频流。不要保证可以无需完整下载随机读取高质量帧。
- 帧可读性优先于极端压缩；候选数量、每段时长、选帧间距、去重阈值要有安全上限与异常回退。选帧导致异常时尽量保留有信息量的兜底截图并提示用户。
- 避免强制识别内容语义，不调用 VLM 或 OCR；无需让本地模型选“最重要”图片。

### 3.4 PDF 输出规范
- 生成单个 keyframes.pdf；PDF 页面中图片应清楚（常规每页一张），避免数张图缩得太小。
- 首页（或页眉）包含视频标题、作者、原始 URL、处理日期、视频时长、截图数量。
- 每张截图写明 HH:MM:SS 时间戳；最好生成可点击的指向该时间点的 B站 URL；能做则加 PDF 内书签。
- 图片使用 Pillow 适当压缩，PDF 使用 ReportLab，先保证 720P/1080P 来源的 PPT 字可辨认，再追求文件大小。
- 关键帧需要视觉去重和基本模糊/黑屏过滤；不因“目标 5MB”而损坏图像。避免重复嵌入相同图像导致 PDF 虚胖。
- 没有有意义图像时，标记 visual_skipped；默认是否跳过 PDF 由规则判断并在输出显示；发生解析/解码错误不应伪装成“无需 PDF”。
- NotebookLM 上传应同时包含音频和 PDF 两种不同来源；PDF 时间戳与音频的内容不能保证 NotebookLM 自动精确对齐。
- 生成 PDF 后用解析库/基础校验确认页数 > 0、文件完整、大小合规。

### 3.5 NotebookLM 自动导入
- 优先复用 https://github.com/teng-lin/notebooklm-py；先验证真实版本、许可证、SDK 创建/上传/轮询的最新方法签名，避免仅根据历史示例直接调用。
- 采用 SDK 包装层 NotebookAdapter（认证检查、创建本子、添加文件、查询来源状态、获取笔记本 URL），业务层不得直接到处写私有协议请求。
- 未认证时先报错；不要在媒体下载完成后才发现 Google Cookie 已失效。
- 标题默认根据 B站视频 title，必要时加“B站｜”前缀，截断为服务端可接受长度并防止空标题。
- 创建成功即持久记录 notebook_id；每个源上传后立即保存 source_id、类型、文件路径和上传状态。
- 明确区分“HTTP 请求接受/已入库/转录及索引完成”。轮询至 SDK 可判断的 READY 状态，超时后保留任务与 Notebook ID 以便 resume。
- 已成功的 source 不能因另一文件失败而重复上传；重试应先查询 Notebook 中是否存在对应来源，避免重复来源。
- 发生 SDK 限流、认证过期、Google 接口变化时返回可追踪错误，不无限重试，支持固定上限+退避策略。
- 最终 URL 使用 SDK 返回或验证过的 Notebook ID 构造，在本机浏览器中可以打开；只标记 ready 不保证手机端立刻刷新。
- 一个视频默认创建一个新笔记本；首版不实现自动合并到现有本子（可在后续版本添加）。
- 来源不会自动分享到他人；只使用用户本人授权账号。

## 4. 作业管理、幂等与离线输出
- 转换与上传使用同一个 pipeline，convert 在产出本地文件后停止，import 在此基础上继续。
- 以 SQLite 存储 Job（推荐），至少包含 job_id、video_key、requested_mode、create_time、last_update、paths、notebook_id、source_ids、状态、error_code 和简短诊断。
- 同一 video_key + Google 账号配置 + 输出模式，重复 import 默认复用任务/返回已有 notebook_url；除非用户明确 --force-new。
- 各步骤完成后保存 checkpoint；CTRL+C 或崩溃后可重启继续。
- 转换中间文件用临时目录及原子移动完成输出；避免一半生成的音频/PDF被当作完整缓存。
- import 成功后可自动清理大体积临时原视频；音频/PDF默认短期留存或按文档可配置保留，失败时必须保留并记录路径。
- 未来远程调用仅预留 --json、稳定退出码与非交互行为；本版本不设计任何网关实现。

## 5. 安全、隐私与凭证管理
- Google Cookie/CSRF/会话导出保存在 notebooklm-py 指定的本机授权目录，权限严格限制（尽量 0700/0600）；不得拷入 Projects、git、输出 JSON、日志或错误报告。
- 真实账号登录必须由用户在本机自主完成；开发/测试不可要求用户发 Cookie 给模型。不要自动提取整个 Chrome 用户数据目录。
- 可用 SDK 提供的 profile/login/refresh，但不要默认启用长期 Master Token；如确需启用必须先评估及征求用户许可。
- B站 Cookies 与 NotebookLM Cookie 分开；URL 校验和重定向校验防止恶意 URL、命令注入、路径穿越。
- subprocess 使用参数列表，不拼 shell 命令。外部程序有超时、退出码和输出长度限制。
- 日志不输出 Cookie、Authorization、完整请求头或隐私路径内容；对于故障保留必要的错误码和 SDK 版本。
- .gitignore 忽略 .env、credentials、storage_state、*.sqlite、job-state、media、cache、tmp、output、日志。
- 不把本地用户 Cookie 放入 LLM prompt，也不通过聊天平台或公网发送。

## 6. 技术选型（先探测本机环境，缺失再安装）
- Python >=3.11；推荐 uv 管理虚拟环境和锁文件，pyproject.toml 项目；Typer CLI。
- yt-dlp 作为 B站解析器；FFmpeg 和 ffprobe 作为系统工具，优先 Homebrew 安装（操作前审查环境，避免重复安装/覆盖现有配置）。
- OpenCV 与 PySceneDetect 进行选帧；Pillow 做图片质量/压缩；ReportLab 制 PDF。
- notebooklm-py 作为 NotebookLM 非官方适配层；版本锁定并编写 contract tests。
- SQLite 标准库管理任务状态；Pydantic 可选（不要为小工具引入不必要的后台服务框架）。
- pytest + ruff 等常规工程质量工具。
- 参考：https://github.com/RexTSLO/video-slide-notes （算法思路，不整体 fork/引入 Whisper）
- 备选参考：https://github.com/ShuboLiang/bili-cli （预览图思路，不必引入 Rust）
- 依赖许可证与具体 API 应由 Claude Code 在开发时重新核验并记录，尤其 ffmpeg 的分发方式及非官方 NotebookLM 的稳定性。

## 7. 建议源码与本地运行数据位置
项目源码：
  ~/Projects/Bili2NotebookLM/
    docs/PRD.md
    CLAUDE.md
    pyproject.toml
    src/bili2nb/{cli.py,config.py,pipeline.py}
    src/bili2nb/bilibili/{parser.py,downloader.py}
    src/bili2nb/media/{audio.py,frames.py,dedup.py,pdf.py}
    src/bili2nb/notebook/{adapter.py,auth.py,uploader.py}
    src/bili2nb/jobs/{db.py,state.py}
    tests/{unit,integration}
    README.md
    .gitignore

运行时数据：优先遵循 macOS 用户数据目录规范（如 ~/Library/Application Support/Bili2NotebookLM），或自定义 ~/.local/share/bili2nb；媒体缓存不要混入 Agent-Memory/Obsidian Vault，也不要写入任何研究结果目录。用户指定 --output 时仅输出成品音频/PDF及不含认证秘密的 manifest。
不会由此对 Windows、NAS、路由器或代理策略做任何修改。

## 8. 质量验收与测试矩阵

**正式可执行验收标准：docs/ACCEPTANCE.md 中 Gate 0–5，优先于下方概要；每关须有 PASS / FAIL / BLOCKED、实际测试证据和报告。未通过时不得声称已验收。**

P0 — NotebookLM API 可用性（正式开发前验证）：
1. 确认用户本机真实 NotebookLM 登录可用，或引导用户一次性本机登录。
2. 使用极小且非敏感的测试 M4A/MP3 和 1 页 PDF 创建临时测试笔记本；只有用户明确同意实际创建/上传后才执行。
3. 检验笔记本创建、两个来源上传、轮询 READY、笔记本 URL；真实 API 发生变化时先调整适配层。
4. 用户确认手机 NotebookLM 可见；如 API 不可用须明确阻塞，而非伪造模拟通过。

P1 — 转换模块：
1. 用短视频、30–60 分钟 PPT 讲座、软件操作演示、纯口播分别测试。
2. 对含多 P 的视频明确选择正确 CID，不静默下载合集。
3. 音频核验完整时长（允许少量容器时间误差）、能播放、无错误转码、上限可控。
4. PDF 页数合理、无明显重复/黑屏、重要新 PPT 不遗漏、文字可辨；生成时间与体积记录真实数据。
5. 模拟无预览图、B站 403、ffmpeg 不存在、无磁盘空间、被中断等故障，输出可定位的错误。

P2 — 两种 CLI 模式：
1. convert URL 可无需 Google 登录成功输出本地文件。
2. import URL 在授权会话下生成新的可访问 NotebookLM 笔记本并上传音频+PDF。
3. 缺失 Cookie、过期 Cookie、上传中断、某来源失败能恢复，不产生重复笔记本或来源。
4. --json 输出格式一致，退出码准确，stdout 不混入进度条或登录秘密。
5. 使用该视频的实际内容在 NotebookLM 提问确认音频转录可用、至少一张 PDF 画面被引用/理解；记录局限，不声称自动时间轴对齐。
6. 最后在手机 NotebookLM 同账号验证新笔记本可见（可能需要等待同步/下拉刷新）。
7. benchmark 给出视频时长、下载字节、音频/PDF体积、每阶段耗时及总耗时，不保证所有视频几分钟完成。

自动化测试策略：
- 单元测试全部使用 mock 数据和本地样本；测试运行默认不联网、不触发真实 Google API。
- 真实 B站、NotebookLM 的集成测试单独标记为手动 opt-in，并限制创建测试资源数量。
- 不将 Google 凭证、个人视频数据、登录状态写入 Git 或测试快照。

## 9. 开发任务拆解与顺序

阶段 0：只读审查 Mac 环境：Python、uv、ffmpeg、yt-dlp、NotebookLM SDK、可用磁盘、网络可访问性。把检查结果和版本记在 docs/ENVIRONMENT.md。不可擅自更改代理/路由器设置。
阶段 1：验证 notebooklm-py：登录流程与小样本真实自动创建/上传/状态轮询；如果此链路暂时不可用，先完成 convert，但在 import 验收中如实标注 blocked。
阶段 2：实现 MVP convert：解析单条 B站 URL，只下音频 + 简单场景/定时抽样 PDF；PDF 生成、缓存和可复现处理过程。
阶段 3：实现 MVP import：NotebookAdapter、保存 ID、失败重试、状态查询、JSON 输出。
阶段 4：优化选帧算法、音频容器处理、重复/动画检测、退避重试、跨视频验证和 CLI 文档。
阶段 5：手工手机验证，记录最终验收结果和未解决风险。

每个阶段都应能运行最小可用例子并有相应测试。优先做出完整“URL→NotebookLM”纵向闭环，再优化视觉算法，不要一开始深陷 PPT 去重算法。

## 10. 首轮执行中的允许与禁止

允许 Claude Code 在本项目目录内创建/编辑源码、文档、测试、虚拟环境、锁文件与临时输出；可使用官方依赖包管理工具安装必要项目依赖并做只读环境检查。

对真实 NotebookLM 的“创建/上传”会消耗用户账号资源：应在开发阶段明确告知并获得授权后再进行首次真实集成测试；真实 Google 登录由用户自行完成。不得删除旧 NotebookLM 内容，不得自动清理云端测试笔记本。

如发现上文接口示例或依赖版本已变，以实时官方文档/当前仓库和实测结果为准，同时更新 PRD 的技术备注，不可默默硬编码不存在的 API。

## 11. 最终交付物

- 可安装、可执行的 Mac CLI，至少实现 bili2nb convert 和 bili2nb import。
- README：安装、首次授权、2 条基本命令、常见故障、Cookie 失效处理、限制。
- docs/ENVIRONMENT.md、docs/TEST_REPORT.md：真实环境、自动化测试与用户同意的端到端结果。
- 可复用的 NotebookLM 适配层、明确的日志/错误码/恢复策略、无泄漏的 .gitignore。
- 不需要 GUI、Bot 插件、Obsidian 笔记导出或持续后台任务。

### V1 最终验收一句话
在已完成一次本机 NotebookLM 授权的前提下，输入任一已验证可访问的 B站单视频 URL，执行 bili2nb import URL，CLI 自动生成音频与有意义的视觉 PDF、创建 NotebookLM 笔记本、上传两个来源并等待处理状态可用，打印可访问的笔记本 URL；用户在同一 Google 账号的手机 NotebookLM 中能找到该笔记本。

