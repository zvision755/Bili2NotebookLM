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
