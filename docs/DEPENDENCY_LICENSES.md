# 依赖许可证核对

本项目以 **MIT** 许可证发布（见 [LICENSE](../LICENSE)，版权人 Ziwei Zhao）。本项目只在运行时**使用**下列依赖（由用户通过 PyPI 安装），**不复制、不修改、不再分发**它们的源码或二进制，因此只需保证它们的许可证允许这种使用方式。

核对日期：2026-11-09；方法：读取已安装包的许可证元数据（`License-Expression` / `License ::` 分类 / `License` 字段）。`tests/unit/test_licenses.py` 在每次 CI 中重新检查：项目元数据一致，且依赖闭包里**没有** GPL / AGPL / LGPL / SSPL / 非商用 / 专有许可证，出现无法识别的许可证也会让测试失败，需要人工复核。

## 直接依赖
| 包 | 版本（锁定） | 许可证 | 用途 |
|---|---|---|---|
| typer | 0.28.0 | MIT | 命令行 |
| yt-dlp | 2026.10.26 | Unlicense（公有领域） | B 站下载（作为库调用） |
| notebooklm-py（含 browser、impersonate extra） | 0.8.6 | MIT | NotebookLM 非官方 SDK |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | 视频解码与帧比较 |
| scenedetect | 0.7.2 | BSD-3-Clause | `--frames scene` 模式 |
| pillow | 13.0.0 | MIT-CMU（HPND） | 读取图片尺寸 |
| reportlab | 4.5.1 | BSD | 生成 PDF |
| pypdf | 7.0.0 | BSD-3-Clause | 校验 PDF |

## 主要的间接依赖
| 包 | 许可证 | 备注 |
|---|---|---|
| playwright 1.64.1 | Apache-2.0 | 仅登录时使用；使用系统 Chrome/Edge 时不会下载浏览器 |
| curl-cffi 0.17.0 / cffi / pycparser | MIT / MIT-0 / BSD-3-Clause | 上传必须使用的传输层 |
| numpy 2.5.4 | BSD-3-Clause（及 0BSD、MIT、Zlib、CC0-1.0 的内嵌组件） | |
| httpx / httpcore / h11 / anyio / idna / click / rich / pygments / platformdirs / filelock / typing-extensions 等 | MIT / BSD / PSF-2.0 | |
| certifi 2026.10.23 | MPL-2.0 | 文件级弱 copyleft；仅使用，未修改、未再分发 |
| tqdm | MPL-2.0 AND MIT | 同上 |
| greenlet | MIT AND PSF-2.0 | |
| shellingham | ISC | |

Windows 上会额外安装少量平台依赖（如 `colorama`，BSD-3-Clause），同样由测试检查。

## 需要知道的两点
1. **FFmpeg**：`ffmpeg` / `ffprobe` 不是 Python 依赖，由用户自行安装并通过命令行调用，本项目**不分发** FFmpeg。另外，`opencv-python-headless` 的预编译 wheel 内部捆绑了 FFmpeg 动态库（LGPL 系列），其包元数据声明为 Apache-2.0；这些库随 wheel 由 PyPI 分发，本项目不另行分发。若你自行打包再分发（例如制作单文件可执行程序），需要单独核对 LGPL 的合规义务。
2. **非官方 API**：`notebooklm-py` 使用未公开的 Google 接口，许可证为 MIT，但使用该接口本身可能违反 Google 的服务条款，见 [SECURITY.md](../SECURITY.md)。
