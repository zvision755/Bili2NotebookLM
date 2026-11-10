# 安全策略 / Security Policy

## 报告漏洞 / Reporting a vulnerability

请**不要**在公开 Issue 里贴漏洞细节、Cookie 或日志。请使用 GitHub 的私密漏洞报告：
仓库页面 → **Security** 标签 → **Report a vulnerability**（<https://github.com/zvision755/Bili2NotebookLM/security/advisories/new>）。

Please do **not** post exploit details, cookies or logs in public issues. Use GitHub's private vulnerability reporting: repository → **Security** tab → **Report a vulnerability**.

> 若该入口暂时不可用（需仓库所有者在 Settings → Code security 中启用），请开一个**不含任何细节**的公开 Issue，只写"需要私下联系渠道"，维护者会提供私密沟通方式。 If the private form is unavailable, open a public issue containing **no details**, only asking for a private contact channel.

报告时请说明：受影响版本/提交、操作系统、复现步骤、影响范围。**不要附带真实的 Google 登录文件或 Cookie**；如日志里可能含敏感信息，先自行脱敏。

## 本工具如何保护 Google 登录凭证

- **登录由你本人在浏览器里完成**。工具不要求、也不接受手工粘贴 Cookie；不使用 `--master-token`、`--browser-cookies`（读取浏览器 Cookie 库）等方式。V1.2 起默认使用 Playwright 管理的 Chromium：首次登录会从 Playwright 的官方下载地址下载该浏览器到用户级缓存目录，**不读取、不修改**你已有的 Chrome / Edge / Safari 及其 Cookie。
- **登录状态保存位置**：由 `notebooklm-py` 保存在 `~/.notebooklm/profiles/<profile>/storage_state.json`（可用环境变量 `NOTEBOOKLM_HOME` 改位置；Windows 为 `%USERPROFILE%\.notebooklm\...`）。该文件包含登录会话 Cookie，等同于"登录凭证"。macOS / Linux 上目录权限为 `0700`、文件为 `0600`；Windows 依赖用户目录的访问控制（ACL）继承。
- **本工具从不读取内容、不打印、不复制、不上传**该文件：`doctor` 只检查"文件是否存在"；任务库（SQLite）只保存任务 ID、视频 ID、笔记本/来源 ID、本地文件路径等，**不含 Cookie、令牌或请求头**；`--json` 的 stdout 只含结果或错误码；错误信息会过滤 SDK 异常文本，不回显凭证。
- **B 站不需要 Cookie**：只处理公开视频，下载不使用任何 B 站登录信息。
- **CI 不使用真实凭证**：GitHub Actions 里没有任何 Google 账号、Cookie 或密钥，也不调用真实 NotebookLM 的创建/上传接口。

## 凭证失效与处理

- 会话过期、被 Google 轮换或被你在账号里退出后，`import` 会在**下载之前**报 `AUTH_REQUIRED`（退出码 20），`convert` 不受影响。
- 修复：运行 `bili2nb setup` 或 `bili2nb auth login`，在弹出的浏览器里重新登录；然后 `bili2nb retry <job_id>`（或直接重新执行 `bili2nb <链接>`，不会重复建笔记本）。
- 想主动清除本机登录：删除 `~/.notebooklm/profiles/<profile>/` 目录（或运行 `python -m notebooklm auth logout`），并在 Google 账号的“安全 → 你的设备”页面检查/退出不需要的会话。

## 绝对不要提交到仓库 / 发给别人的内容

- `~/.notebooklm/` 整个目录、任何 `storage_state*.json`、`*cookies*`、`.env*`、`*token*`、`*secret*`、`*credentials*`；
- 任务库 `jobs.sqlite`（及 `-wal` / `-shm`）、工作目录与输出目录、下载的音视频、生成的 PDF、日志；
- 含真实笔记本链接/ID 的截图或报告。

`.gitignore` 已覆盖以上内容，并有自动化测试（`tests/unit/test_gitignore.py`）防止回退。若不慎提交了凭证：**先在 Google 账号里退出所有设备会话并重新登录**（使旧 Cookie 失效），再清理仓库历史。

## 非官方 API 风险

NotebookLM 部分依赖**非官方**库 [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py)（本项目固定 0.8.6），它使用未公开的 Google 接口，可能随时失效，甚至触发账号风控或违反服务条款。请自行评估风险，建议使用非关键账号，并避免高频、批量使用。本工具与 Google、哔哩哔哩无关。

## 受支持版本

仅最新发布版本/主分支会收到安全修复。
