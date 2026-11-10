# Bili2NotebookLM V1：阶段验收关卡（Claude Code 必须执行）

> 本文是 docs/PRD.md 第 8–9 节的量化补充；若有冲突，以“安全与真实验收不造假”为前提更新两处文档。
> 仅本地命令行（V1 以 macOS 为首个真实验证平台，V1.1 起 CI 覆盖 macOS / Windows / Linux）。聊天机器人、远程网关、笔记软件集成和 GUI 全部排除 V1。
> 所有外部接口与文件格式必须以实际版本、官方说明和真实测试为准。

## 通用执行规则：Stage Gate

- 每一阶段结束必须更新 docs/TEST_REPORT.md，记录日期、环境、命令、测试数据类型、期望、实际、是否 PASS / FAIL / BLOCKED、日志/文件位置与未解决问题。
- PASS 必须基于可复现实测证据；mock PASS 只代表逻辑测试通过，不可代表真实 B站/NotebookLM API 通过。
- FAIL 表示已测试且行为违反硬性验收；BLOCKED 表示缺账号授权、网络、配额、外部接口限制或测试数据，不能伪报成功。
- 未通过的阶段原则上不进入依赖它的后续阶段；若受外部服务阻塞，可在不使用用户凭证的前提下开发独立模块，但必须标记“未完成端到端验收”。
- 涉及新建真实 NotebookLM 笔记本、上传文件前，必须取得用户同意并让用户自行完成授权；不得要求用户贴出 Cookie 或把 Cookie 发到聊天里。
- 每阶段回报：一句结论 + 通过数/失败数 + 关键命令/截图或去敏日志 + 尚存风险。严禁只说“代码已完成”。
- 所有单元测试默认断网、mock NotebookLM、不得创建云端资源；实时集成测试须 opt-in。
- 真实测试的耗时只记录数据，不用“几分钟必须完成”作为硬性通过门槛，因为受网络和 NotebookLM 队列影响。

## Gate 0 — 环境可用与项目初始化（先行）

交付：pyproject.toml、锁文件与可安装 CLI 骨架；docs/ENVIRONMENT.md；安全的 .gitignore。

硬性通过条件：
1. 记录 macOS 架构、Python >=3.11、uv、yt-dlp、ffmpeg、ffprobe、Git、磁盘可用量、DNS/访问 B站与 NotebookLM 的连通性；缺失项明确安装/阻塞状态。
2. 在隔离虚拟环境中完成项目安装；执行 bili2nb --help 返回 0；bili2nb import --help 和 convert --help 能查看真实支持参数。
3. 无权限操作、未授权登录、系统网络/代理设置调整、现有项目修改或远程网关建设。
4. Git 忽略凭证、Cookie、运行日志、音视频、PDF 缓存与 SQLite；审查生成的文件，不能把测试秘密写进 Git。
5. 本阶段不需要访问真实 Google 账号，不会因为账号未登录而 FAIL。

证据：环境版本及路径、安装与 help 命令输出、忽略规则检查结果。
门禁：未能安装/调用 CLI 或核心依赖不可用则 FAIL/BLOCKED，不能报告 PASS。

## Gate 1 — NotebookLM 认证与真实双来源上传（最高优先级风险）

交付：NotebookAdapter 最小实现/验证脚本、登录与检查命令、SDK 兼容性记录。

硬性通过条件：
1. 用户在本机完成一次交互式 Google 授权后，bili2nb auth check 能实际验证会话；登出/无会话时给 AUTH_REQUIRED，不泄漏敏感字段。
2. 经用户明确同意后，使用非敏感、已知含可识别语音的短音频及有少量可见文字的一页 PDF：创建一个全新 NotebookLM 笔记本，并取得 notebook_id 和直达 URL。
3. 音频和 PDF 分别上传，各取得不同的 source_id；轮询处理状态直到两个来源均 READY（或该 SDK 官方定义的等价可用状态）；严禁仅凭上传请求返回 200 即认定成功。
4. 在 NotebookLM 网页端实际打开该 notebook URL，确认两个来源出现且可用于问答；至少对音频内容问一题、对 PDF 明确文字问一题，并记录结果。手机端不作为验收条件（V1.1 起取消）。
5. 对失效会话、上传超时分别能给出可诊断错误，避免再次运行时静默重复创建笔记本。
6. SDK 的当前版本、接口方法/签名、授权配置位置、非官方 API 风险记录到 docs/ENVIRONMENT.md；不复制秘密内容到项目。

证据：去敏的 notebook_id/source_ids、两个 READY 状态、网页人工核验描述、命令输出、测试耗时。
门禁：如果缺用户授权或 Google 外部接口不可用，标记 BLOCKED；mock 成功不能代替本 Gate PASS。

## Gate 2 — bili2nb convert 真正可用（本地素材）

交付：单链接解析、独立音频获取、视觉取帧、keyframes.pdf、manifest、convert 命令。

硬性通过条件：
1. 输入一条用户有权使用的公开 B站视频 URL，可输出可播放音频和非空 PDF（若该视频确无有意义画面，需显式选择 audio-only / 记录跳过原因）；convert 不依赖 Google 登录。
2. yt-dlp 首选音频流下载；ffprobe 检查文件有音频流且时间长度与视频元数据误差不超过 max(2 秒, 视频时长的 1%)；不得默默截断长音频。
3. 音频原始格式受 NotebookLM 支持时优先保留或 remux；不因“压缩”无条件重编码；实际文件扩展名与流/容器相符，且符合当前上传大小限制。
4. PDF 可被标准阅读器打开，有可选择文本的标题、原视频 URL、至少一张清晰截图、HH:MM:SS 时间戳；截图时刻在视频时长内。PPT 图片中的原有文字在测试样例的正常阅读缩放下应可辨认。
5. 处理具有多 P 的链接时只下载指定分 P；不意外下载整个合集；b23.tv 短链接至少测试一个安全重定向。
6. 至少测试短视频、课件讲座或软件演示其中两类；记录音频大小、PDF 页数、PDF 大小、帧数量及真实分阶段耗时。
7. 错误链接/不可用视频/缺少 ffmpeg/解码失败返回非 0 且有明确错误码；临时半成品不能被标作完整缓存。

证据：convert CLI 完整命令、ffprobe 输出、PDF 页面截图或人工目检、文件统计、异常路径测试。
门禁：没有真实视频转换产物或音频不完整，则 FAIL。

## Gate 3 — bili2nb import 端到端自动入库

交付：import 命令、NotebookAdapter、明确 Notebook/Source ID 持久化、status/retry 基本实现。

硬性通过条件：
1. 在已有授权且用户同意真实上传后，执行单条 bili2nb import URL，不需要人工拖文件、浏览器点击或 Whisper；自动完成 Gate 2 的转换。
2. 在下载媒体前预检 NotebookLM 登录；新建笔记本标题与视频标题相关，保存真实 notebook_id；音频与 PDF 生成两条来源，每条 source_id 持久保存。
3. 默认等待两条来源都 READY 才 exit 0；输出 notebook_url，手动打开能看到实际来源。如选择 audio-only，必须明确显示只有一个来源且原因/模式。
4. 用户中途 Ctrl+C、上传任一来源失败、超时后可用 bili2nb status JOB_ID 和 bili2nb retry JOB_ID 恢复；未失败的来源不得重复上传，原 notebook_id 需复用。
5. 相同视频/同分 P/同账号配置再运行 import，默认返回或继续原 Job，不自动新建重复 Notebook；只有 --force-new 可重建。
6. --json stdout 只有一个可解析 JSON 对象，含 job_id/status/notebook_id/notebook_url/source_ids、错误码或警告；进度和诊断输出到 stderr，不能包含 Cookie、凭证或完整请求头。
7. auth check、convert、import、status、retry 等已公开命令均有自动化测试；不支持的选项不应在 --help 中装作可用。

证据：至少一次真实 B站 URL -> NotebookLM 新笔记本的完整执行记录、Notebook URL、2 READY 状态、同链接二次运行不重复的检验、失败恢复模拟测试。
门禁：只有完成文件转换、没有自动创建和上传时，不得宣称 Gate 3 PASS。

## Gate 4 — 可靠性、视觉效果及性能回归

交付：稳健的失败回退、选帧去重、错误处理、可用 README、完整自动化测试。

硬性通过条件：
1. 至少 3 种实际样本：PPT 讲座、屏幕教程/实验演示、普通口播；其中至少一条 30–60 分钟长视频。记录每种实际处理耗时、CPU/内存峰值（可测则记）、音频/PDF 大小、截图数量。
2. 人工给 PPT 样本建立一份关键页面清单，计算“应保留的明显完整新页”召回率；首版目标 >=90%，重复/过渡/全黑截图占比目标 <=10%。这是特定测试集的验收目标，不保证所有公开视频都满足。
3. 截图 PDF 中 PPT/表格/代码文字清晰，不为缩小体积造成肉眼不可辨。PDF 大小 5–15 MB 是参考目标，**绝非必须牺牲可读性达到的硬上限**。
4. SceneDetect 失效或缺预览图时能够退回简单定时取帧/视频流；必须给出 warning，不得把技术故障悄悄解释为无视觉内容。
5. 测试 B站限速/403、ffmpeg 缺失、磁盘不足、Google auth 失效、NotebookLM 429/5xx、上传卡死、SIGINT；错误码明确，限次退避，保留可恢复的 Job，不能出现无限循环。
6. pytest 所有默认离线测试通过，ruff 检查通过，README 的安装、登录、convert/import、修复登录失效步骤能在干净虚拟环境复现。
7. 不引入本地 Whisper/VLM/OCR、Agent 依赖、后台服务或非 V1 远程功能。

证据：测试表、人工截图对照、错误注入结果、pytest/ruff 原始结果、真实 benchmark 数据。
门禁：出现数据丢失、明文凭证泄漏、严重重复建本或无法恢复的正常网络中断时 FAIL；算法阈值不达标则记录并优化或显式请求调整目标，不能自称通过。

## Gate 5 — 最终用户验收（V1 可交付）

交付：可安装 CLI、README、docs/ENVIRONMENT.md、docs/TEST_REPORT.md、当前已知限制。

硬性通过条件：
1. 用户使用与日常一致的桌面环境（macOS / Windows / Linux），首次手动授权一次后，在已登录的状态下执行 bili2nb import <公开可访问B站URL>。
2. 无任何手动导入/总结/转录步骤，CLI 返回非空且可打开的 NotebookLM URL；两条来源 READY，真实网页 NotebookLM 可对视频语音和至少一个视觉信息点进行准确问答。
3. （V1.1 起取消）手机端 NotebookLM 不再是必须验收项：同步由 Google 负责，不在本工具可控范围内。如需自愿验证，仅记录实测，不设同步 SLA，不影响 PASS 判定。
4. 使用第二条不同的视频再次成功，确认并非单个固定样本特例；第一次测试后重新启动 CLI/终端，无需重复登录（只要 Cookie 仍有效）。
5. 断开/清除本机认证状态时，convert 依然成功、import 明确报 AUTH_REQUIRED；重新登录后可恢复。
6. 按 README 从项目目录运行安装、help、auth、convert、import 的示例确实可复现；最终 docs/TEST_REPORT.md 对每一个 Gate 标注 PASS/FAIL/BLOCKED 并保留证据索引。
7. 未经用户授权不得删除云端笔记本或来源；本地 Cookie/登录状态不得进入仓库、日志、导出包。

一票否决：把 mock 当实测、拿上传成功冒充来源 READY、重复创建大量 NotebookLM 笔记本、泄漏 Google Cookie、必须人工上传才能完成。
最终通过标准（V1.1 修订）：CLI 自动完成导入（无人工上传/转录），两个来源均 READY，且 NotebookLM **网页版**能对视频语音和至少一个视觉信息点正常问答；并有实际端到端证据。

## 验收报告填写模板（每阶段复制一份）

- Gate：0 / 1 / 2 / 3 / 4 / 5
- 结果：PASS / FAIL / BLOCKED
- 执行日期、Mac 环境、代码版本：
- 验收命令（含必要参数，不含敏感信息）：
- 输入样本类型、长度、来源授权说明：
- 自动化测试数量：通过 / 失败 / 跳过
- 真实外部 API 调用：是否经过用户批准；请求摘要（去敏）
- 产物路径、来源 ID 与 READY 状态（公开报告中不写真实 Notebook ID/URL，用别名代替）：
- 截图质量、音频完整度、PDF 大小、阶段耗时：
- 缺陷与风险、下一步措施：
- 验收执行人 / 用户确认事项：

## Gate 6 — V1.2：跨平台登录与多 P 批量导入

范围与设计见 [V1.2_DESIGN.md](V1.2_DESIGN.md)。每条需在 TEST_REPORT.md 留下证据；**PASS / FAIL / BLOCKED 如实标注，Mock 测试不得冒充真实验收**。

登录：
1. 三个平台的 `setup` 与 `auth login` 默认行为一致（Playwright Chromium）；显式 `--browser chrome|msedge` 仍可用。（离线单元测试 + CI 三平台 Chromium 安装/启动任务）
2. 没有 Chrome / Edge 的机器能完成登录。（**需要真实干净机器**；CI runner 自带浏览器，只能证明捆绑 Chromium 可安装、可启动。）
3. 下载失败、启动失败、未完成登录各有明确错误与重试方法；未登录时 import 报 `AUTH_REQUIRED`；不读取/修改用户已有浏览器配置。

多 P（离线，Mock NotebookLM）：
4. 单 P：1 笔记本 2 来源；3 P：1 笔记本 6 来源；指定 P2：1 笔记本 2 来源；`--audio-only`：3 个音频来源；部分 P 无画面：该 P 仅音频并有 warning。
5. 重复导入全部 P 复用原任务与笔记本；`--force-new` 只新建 1 个笔记本；全部 P 任务不会命中旧的单 P 任务；分 P 列表变化时报 `PARTS_CHANGED` 而不是静默复用。
6. P2 下载失败：不创建云端笔记本，P1 产物复用；P2 上传失败：retry 不重建笔记本、不重传 P1；上传中 Ctrl+C 后 retry 只继续未完成来源。
7. 超过来源上限：只合并到刚好不超限——连续分 P 切成 g 个均衡的组（大小相差 ≤ 1），每组音频、PDF 各合并成 1 个来源且**分组边界对齐**，g 取使来源总数 ≤ 上限的最大值（例：上限 50、30 个分 P → 25 音频 + 25 PDF；132 个分 P → 每组 5 或 6 个）；仍只有 1 个笔记本；某组合并文件超 190 MB：该组对半拆开；体积允许的最少来源数仍超限：创建笔记本之前失败（退出码 27）。`--merge-sources` 仍是显式的“并成最少来源”；`import --source-limit N`（N ≥ 2）只会把有效上限压得更严（模拟免费账号），`limit_origin` 记为 `override`。
8. 合并音频总时长与各 P 之和相符、可完整解码；合并 PDF 页序与书签正确；上传文件均 < 200 MB；来源数不超过账号上限。
9. 旧版（V1.0/V1.1）SQLite 任务库可正常打开，旧任务 status/retry/jobs 继续有效。
10. `--json` 完整表达批次、分 P、来源状态；stdout 仅一个 JSON 对象；不含 Cookie/登录状态。
10a. `--part` 范围/列表（V1.2.1，离线）：`8` / `8-27` / `1,3,5` / `1-3,7` 解析正确（去重、升序）；`0`、负数、`5-3`、`a`、`1-`、空串、`1,,2` 报明确中文错误（`INVALID_URL`，退出码 10，且发生在任何网络调用之前）；单个数字沿用单 P 老流程与旧键；子集有独立且稳定的任务键，不命中 `BV_all`/单 P 任务；子集导入 1 笔记本、2N 来源，重复导入复用，`--force-new` 只新建 1 个；`convert` 同语法；`--json` 纯净。

真实验证（**需要用户授权**，未授权前为 BLOCKED）：
11. 用一个 2–3 P 的短视频真实导入一次：单个笔记本、各来源 READY、读取到真实的 `source_limit`。
    - **状态更新（2026-11-10，第三次真实运行）：PASS（自动核对 + 用户网页问答反馈“可以答对”）。** 用 `BV1z3NY66EY1` 的 P8–P27（`--part 8-27`，20 个分 P）真实导入：1 个笔记本（30→31）、40 个来源全部 READY、`source_limit`=300/server、云端 40/40 标题一致、幂等重跑 `reused: true`；用户在网页里做了 4 类问题（来源能打开、语音内容题、画面信息点题、跨分 P 题），反馈“可以答对”——这是用户的概括反馈，未逐题记录引用了哪些来源。详见 TEST_REPORT.md“V-2 第三次”。
    - 此前状态（前两次，2 P 视频）：尚未 PASS。 首次真实导入 FAIL（P02 被同名复用吞掉，云端 2/4 个来源却报成功），已修复；复验时 NotebookLM 把几乎无语音的 P02 音频处理为 ERROR、工具如实失败。`source_limit` 已真实读到（300 / server）。待用信息量大、有语音的 2–3 P 视频重新验证，详见 TEST_REPORT.md“V-2 真实多 P 导入记录”。
12. Windows / Linux 真实登录与导入。
13. 免费额度模拟 + 长视频（V-4，2026-11-10）：`BV1z3NY66EY1` 全部 132 个分 P，`--source-limit 50`。**PASS（自动核对 + 用户网页问答）**：1 个新笔记本、50 个来源全部 READY、`limit_origin=override`、25 组（5/6 个分 P）音频/PDF 分组对齐、最大来源 56.6 MiB、4 次瞬时网络错误均自愈、成功后精确清理释放 2.6 GB、幂等复跑 `reused: true`。发现并修复缺陷：上传中断残留 1 个 PREPARING 空壳（`sweep_stuck_stubs`）；已存在的那个空壳经用户同意后删除，云端读回恰为 50 个来源、全部 READY。用户网页问答：11 题均通过、质量可接受（概括反馈，未逐题记录引用来源）；用户确认笔记本 50 个来源、音频与 PDF 成对且边界对齐。详见 TEST_REPORT「V-4」。

离线验收（已自动化，`tests/unit/test_cleanup.py`）：
13. **导入后本地清理是精确的、以云端核对通过为前提的**：任务 COMPLETED 且本次已核对云端包含全部记录的来源，才清理；只清本任务用到的分 P 目录（含缓存复用的）与 `_merged/`；`<BV>/` 与批次清单仅在不再含其它分 P 时删除，同一 `<BV>/` 里未选中的分 P（`convert` 的产物）和无关文件原样保留；`--keep-files` / `--output`、失败 / 中断 / 云端核对失败 / 复用均不清理；符号链接与输出根之外的路径不会被删；清理失败只是 warning、不影响导入成功；`bili2nb clean` 默认 dry-run、`--yes` 才删，不碰 running、最新失败任务（除非点名 job id）与用户保留位置，`--json` stdout 纯净。真实导入后的清理效果留待下次真实导入时人工观察。

一票否决：多 P 导入创建了多个笔记本、重复上传已成功的来源、把 Mock 当真实验收、泄漏 Cookie。
