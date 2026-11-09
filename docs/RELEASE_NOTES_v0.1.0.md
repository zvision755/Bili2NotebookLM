# bili2nb v0.1.0 — Release Notes（草稿，尚未发布）

> **状态：草稿。** 是否发布由仓库所有者决定；发布前请先看文末的"发布前检查清单"。

把一个公开的 B 站视频链接（或 BV 号）一步导入你自己的 NotebookLM：自动提取音频和关键画面 PDF，新建一个笔记本，上传两个来源，等待 READY，输出笔记本链接。转录和总结由 NotebookLM 自己完成，本地不运行 Whisper、OCR 或任何大模型。

```bash
bili2nb <B站链接 或 BV号>          # 也可以写成 bili2notebooklm
bili2nb convert <链接或BV号>       # 只在本地生成音频 + 关键帧 PDF，不需要登录 Google
bili2nb doctor                    # 检查本机环境
```

## 亮点
- **一条命令**：`bili2nb <URL|BV号>`；首次使用引导设置工作目录与 Google 登录；macOS / Windows / Linux 各有默认工作目录且可修改；装成全局命令，无需激活虚拟环境。
- **关键帧 PDF**：OpenCV 稳定幻灯片检测（忽略烧录字幕区）、去重、过少时补充定时取帧并给出 warning；PDF 嵌入 CJK 字体（NotebookLM 预览不乱码）；每页带可点击的时间戳。
- **音频不重新压缩**：AAC/MP3 原样保留；超过 NotebookLM 单文件 200 MB 上限才转码，绝不截断。
- **可靠上传**：2 MB 分块断点续传；服务端已接收但回执丢失时先核对云端再决定是否重传。
- **幂等与可恢复**：同一视频再次导入直接返回原笔记本；失败后 `bili2nb retry <job_id>` 从检查点继续；对限流/网络错误有上限的指数退避。
- **安全失败**：notebooklm-py 版本/私有接口不兼容时，在任何云端写入之前停止（`SDK_INCOMPATIBLE`，退出码 26）；清理孤儿来源需要归属证据，不会仅凭同名文件就删除。
- **`bili2nb doctor`**：只读自检，缺什么就告诉你怎么装。

## 已验证与尚未验证

| 内容 | 状态 |
|---|---|
| macOS：真实 Google 登录、真实 NotebookLM 导入（3 条不同视频，含 38 分钟讲座）、真实 B 站下载、网页版问答 | **已验证** |
| macOS / Windows / Linux：Ruff、离线 Pytest（Python 3.11 + 3.12）、`doctor`、`uv tool install` 全局命令冒烟测试、未登录时 `AUTH_REQUIRED` 退出码 | **已在 GitHub Actions 验证**（以最新一次 CI 为准） |
| Linux：README 安装块在干净的 Ubuntu 上逐字执行 | **已在 GitHub Actions 验证** |
| Windows / Linux：真实 Google 浏览器登录、真实 NotebookLM 导入、真实 B 站下载 | **尚未验证**（CI 没有账号） |
| Windows：README 中的 `winget` 软件包 ID | 已核对包 ID 存在，**尚未在真实 Windows 上执行** |
| Python 3.13 / 3.14 / 3.15 | 未纳入 CI（开发机曾测过 3.14.7） |
| 手机端 NotebookLM 同步 | 由 Google 负责，**不属于**验收范围 |

## 已知限制与风险
- **非官方 API**：NotebookLM 部分依赖非官方库 `notebooklm-py`（固定 0.8.6）。它使用未公开的 Google 接口，可能随时失效，甚至违反服务条款；请自行评估风险，不要批量/高频使用。详见 [SECURITY.md](../SECURITY.md) 与 [SDK 私有接口清单](SDK_PRIVATE_API.md)。
- 只处理你有权访问的**公开**视频；请遵守 B 站规定与版权；媒体文件只保存在本机。
- NotebookLM 单个上传文件上限 200 MB；音频超限时会明确报错，不会截断。
- 关键帧选取是启发式的，不保证覆盖所有视频类型；检测不足时会补充定时取帧并给出 warning。
- 本工具与 Google、哔哩哔哩无关。

## 凭证与隐私
- Google 登录由你在浏览器里完成，工具不接受手工粘贴的 Cookie；登录状态保存在 `~/.notebooklm`，**本工具不读取、不打印、不复制**；任务库与 `--json` 输出不含 Cookie。
- 不要提交或分享：`~/.notebooklm/`、`storage_state*.json`、`*cookies*`、`.env*`、`jobs.sqlite`、工作目录、媒体与日志。`.gitignore` 已覆盖并有自动化测试。

## 安装
见 [README](../README.md) 顶部的 Windows / macOS / Linux 快速开始。需要 `uv`、`ffmpeg`、`git`。

## 许可证
MIT © 2026 Ziwei Zhao；依赖许可证核对见 [DEPENDENCY_LICENSES.md](DEPENDENCY_LICENSES.md)。

## 发布前检查清单（给仓库所有者）
- [ ] 决定旧提交中残留的笔记本链接如何处理（见 [V1.1_HARDENING.md](V1.1_HARDENING.md) §2.2）。
- [ ] 在 GitHub Settings → Code security 启用 **Private vulnerability reporting**（`SECURITY.md` 依赖该入口）。
- [ ] 在真实 Windows 电脑上按 README 完整走一遍安装与导入，并把结果补进测试报告。
- [ ] 确认最新提交的 GitHub Actions 全部通过，再创建 tag `v0.1.0` 并发布 Release。
