# Claude Code 项目执行规则

本目录是 Bili2NotebookLM CLI 项目（V1 功能，V1.1 起做稳定性维护）。先完整阅读 docs/PRD.md 和 docs/ACCEPTANCE.md，然后严格按照 Gate 0–5 的量化验收关卡开发。不要在另一个目录重复建项目。每关在 docs/TEST_REPORT.md 留下可复现证据，PASS / FAIL / BLOCKED 必须如实报告；不得跳过 Gate 或把 mock 当真实 NotebookLM 端到端验证。

## 核心产品
- 入口：bili2nb import <B站视频URL>。
- 已经完成一次本地 NotebookLM 登录时，CLI 自动准备音频和必要的关键帧 PDF、创建新笔记本、上传来源、等待 READY 并返回 NotebookLM URL。
- bili2nb convert <URL> 仅本地输出素材，不需要 Google 登录。
- NotebookLM 自行转录音频；本地不运行 Whisper、不做 LLM 总结、不开发远程 Bot、GUI、插件和 Web 服务。

## 开发纪律
1. 先只读检查本机环境，记录 docs/ENVIRONMENT.md；再优先验证 notebooklm-py API 创建和上传的可行性。
2. 对 Google 账号实际创建/上传测试前，向用户告知并取得明确同意；一次性浏览器登录由用户自行完成。切勿输出或提交 Cookie。
3. 分阶段实现并测试：最小端到端闭环优先，视觉算法随后优化。每阶段更新开发记录、已验证事实与阻塞项。
4. 引用外部项目时重新核验当前 API、发布版本、许可证与维护状态，优先官方 README、源代码、测试与 release notes。
5. 所有源码和文档限制在本项目目录。媒体缓存、Cookie 和用户会话状态不得进入 Git；不要改动本项目目录之外的文件，也不要改动系统网络/代理设置或系统登录状态。
6. 仅真实成功后报告成功；SDK 模拟测试不可冒充真实 NotebookLM 验收。出错保留 Job、Notebook ID、Source ID，支持幂等重试。
7. 不要提早实现聊天机器人、笔记软件集成、远程网关或远程访问。

## 完工输出
提供可安装 CLI、README、单元测试、docs/ENVIRONMENT.md、docs/TEST_REPORT.md；在终端展示 convert 与 import 的实际命令及可验证结果。
