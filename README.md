# bili2nb

[![CI](https://github.com/zvision755/Bili2NotebookLM/actions/workflows/ci.yml/badge.svg)](https://github.com/zvision755/Bili2NotebookLM/actions/workflows/ci.yml)

把一个公开的 B 站视频链接，一步导入你自己的 NotebookLM：

```
bili2notebooklm <B站链接 或 BV号>
```

CLI 会自动下载音频、抽取关键画面生成 PDF、**新建一个笔记本**、上传这两个来源、等待 NotebookLM 处理完成（READY），最后打印笔记本链接。转录和总结都由 NotebookLM 自己完成，本地不跑 Whisper、不调用任何 LLM。

> 本机命令行。**macOS** 做过完整真实验证（含真实 NotebookLM 导入）；**macOS / Windows / Linux** 都由 GitHub Actions 自动测试（安装、离线测试、`doctor`、全局命令启动），但 Windows、Linux 上的真实 Google 登录与 NotebookLM 导入尚未验证。只处理你有权访问的公开视频；请遵守 B 站与版权规定，媒体文件只保存在本机。
> NotebookLM 部分依赖**非官方**库 [notebooklm-py](https://github.com/teng-lin/notebooklm-py)（已固定 0.8.6），Google 可能随时改动接口导致失效。

## 快速开始

需要先装三样东西：**uv**（Python 工具管理器）、**ffmpeg**、**git**。下面每一步都只需做一次。

### Windows（PowerShell）

```powershell
# 1. 安装 uv、ffmpeg、git（装完请关闭并重新打开 PowerShell）
winget install --id astral-sh.uv -e
winget install --id Gyan.FFmpeg -e
winget install --id Git.Git -e

# 2. 获取代码并安装（装成全局命令，不需要激活虚拟环境）
git clone https://github.com/zvision755/Bili2NotebookLM.git
cd Bili2NotebookLM
uv tool install . --overrides overrides.txt
uv tool update-shell          # 然后再关闭并重新打开 PowerShell

# 3. 检查环境（缺什么会告诉你怎么装）
bili2nb doctor

# 4. 首次设置（选工作目录 + 登录 Google），之后直接用
bili2nb setup
bili2nb BV1VV4y1V7Pz
```

Windows 默认用系统自带的 **Edge** 登录 Google；想用 Chrome 就 `bili2nb setup --browser chrome`。工作目录默认是 `%APPDATA%\Bili2NotebookLM`。

### macOS（终端）

```bash
# 1. 安装 uv、ffmpeg、git（用 Homebrew；没有 Homebrew 先装：https://brew.sh）
brew install uv ffmpeg git

# 2. 获取代码并安装（装成全局命令，不需要激活虚拟环境）
git clone https://github.com/zvision755/Bili2NotebookLM.git
cd Bili2NotebookLM
uv tool install . --overrides overrides.txt
uv tool update-shell          # 然后重新打开终端

# 3. 检查环境
bili2nb doctor

# 4. 首次设置（选工作目录 + 登录 Google），之后直接用
bili2nb setup
bili2nb BV1VV4y1V7Pz
```

macOS 默认用 **Chrome** 登录；没有 Chrome 就 `bili2nb setup --browser msedge`。工作目录默认是 `~/Library/Application Support/Bili2NotebookLM`。

### Linux（Debian / Ubuntu 为例）

```bash
sudo apt update && sudo apt install -y git curl ffmpeg fonts-wqy-microhei   # fonts-wqy-microhei：让 PDF 封面中文能嵌入字体
curl -LsSf https://astral.sh/uv/install.sh | sh      # 安装 uv，装完重新打开终端
git clone https://github.com/zvision755/Bili2NotebookLM.git
cd Bili2NotebookLM
uv tool install . --overrides overrides.txt
uv tool update-shell                                  # 然后重新打开终端
bili2nb doctor
bili2nb setup                                         # 登录 Google 需要桌面环境里的 Chrome 或 Edge
```

### 装好之后

```bash
bili2nb <B站链接 或 BV号>          # 直接导入到你的 NotebookLM（命令名也可以写成 bili2notebooklm）
bili2nb convert <链接或BV号>       # 只在本地生成音频 + 关键帧 PDF，不需要登录 Google
bili2nb doctor                    # 随时检查环境；加 --online 会真实连一次 NotebookLM
```

### 常见问题

- **提示找不到 `bili2nb` 命令**：执行过 `uv tool update-shell` 后要**重新打开终端**；仍不行就运行 `uv tool dir --bin` 看命令装在哪，把那个目录加进 PATH。
- **`bili2nb doctor` 报 ffmpeg 缺失**：装完 ffmpeg 要重新打开终端，让 PATH 生效。
- **更新到新版本**：`git pull`，然后 `uv tool install --force . --overrides overrides.txt`。
- **卸载**：`uv tool uninstall bili2nb`（登录状态在 `~/.notebooklm`，工作目录里是任务记录，需要时可手动删除）。
- Windows / Linux 目前主要靠自动测试（GitHub Actions）验证，真实登录与导入欢迎反馈问题。

## 安装说明（详细）

需要：[uv](https://docs.astral.sh/uv/) 和 `ffmpeg`（macOS：`brew install ffmpeg`；Windows：`winget install ffmpeg`，装完重开终端）。

```bash
cd Bili2NotebookLM
uv tool install . --overrides overrides.txt   # 装出两个同样的命令：bili2notebooklm 和 bili2nb
uv tool update-shell         # 若提示 PATH 没配置，执行一次后重开终端
bili2notebooklm --help
```

**不需要激活虚拟环境，也不需要进入项目目录**：`uv tool install` 会给本工具单独建一个隔离环境（依赖都装在里面，不污染系统 Python），并在 `~/.local/bin`（Windows 为对应的 uv 工具目录）放一个启动器，启动器直接指向那个环境里的 Python。所以装完后随便开一个终端，输入 `bili2nb ...` 就能用，和 yt-dlp 一样。

`--overrides overrides.txt` 用来去掉 scenedetect 附带的重复 OpenCV 包（`opencv-python` 与 `opencv-python-headless` 共用同一个 `cv2` 目录，会互相冲突）。

更新代码后重装：`uv tool install --force . --overrides overrides.txt`。卸载：`uv tool uninstall bili2nb`。

## 用法（像 yt-dlp 一样）

```bash
bili2notebooklm "https://www.bilibili.com/video/BV1VV4y1V7Pz"   # 链接
bili2notebooklm BV1VV4y1V7Pz                                    # 或只给 BV 号（也支持 av 号）
bili2notebooklm BV1VV4y1V7Pz?p=3                                # 分 P
bili2notebooklm --json --keep-files BV1VV4y1V7Pz                # 选项放前面也行
```

直接跟链接/BV 号就等于 `import`：下载音频、生成关键帧 PDF、**新建一个笔记本**、上传、等待 READY，最后打印笔记本链接。同一视频再次执行会直接返回原笔记本，不会重复创建。

### 第一次使用：设置工作目录和 Google 登录

第一次在终端里运行时会自动引导（也可以随时手动执行 `bili2notebooklm setup`）：

1. **工作目录**：存放任务记录和临时文件。回车使用默认位置，也可以输入别的路径：
   - macOS 默认：`~/Library/Application Support/Bili2NotebookLM`
   - Windows 默认：`%APPDATA%\Bili2NotebookLM`
   - 想改位置：再运行 `bili2notebooklm setup`，或直接 `bili2notebooklm setup --work-dir D:\bili2nb`；也可用环境变量 `BILI2NB_HOME`（优先级最高）。
2. **Google 登录**：弹出浏览器，你自己登录 Google；工具只保存登录状态，不经过本工具、不进日志。之后无需重复登录（登录失效时会再提示）。不需要手工复制 Cookie，B 站公开视频也不需要 B 站 Cookie。

非终端环境（脚本/`--json`）不会弹任何提问，缺登录时返回错误码 `AUTH_REQUIRED`。

### 只做本地转换（不需要登录 Google）

```bash
bili2notebooklm convert BV1VV4y1V7Pz --output ./out      # 产物：audio.m4a、keyframes.pdf、manifest.json
```

其他选项：`--part N`、`--frames auto|slides|scene|none`、`--audio-only`、`--keep-files`、`--force-new`（为同一视频另建笔记本）。

> Windows 说明：默认目录、ffmpeg 提示和登录流程已按 Windows 适配，但本项目目前只在 macOS 上做过真实验证；Windows 上若遇到问题请反馈。

## 依赖

| 类别 | 依赖 | 说明 |
|---|---|---|
| Python | **≥ 3.11** | CI 验证 3.11 与 3.12（Ubuntu、Windows；macOS 为 3.12）；开发机另测过 3.14.7；3.13 / 3.15 未实测 |
| 系统工具 | **ffmpeg + ffprobe**（实测 9.0.2） | 用于音频校验/必要时转码；不会随 Python 包安装，需自行装 |
| B 站下载 | `yt-dlp`（Python 包，锁定 2026.10.26，要求 ≥ 2026.1.1） | 作为库调用，**不需要单独装 yt-dlp 命令**；B 站改版时升级它即可 |
| NotebookLM | `notebooklm-py[browser,impersonate]` **==0.8.6**（非官方，MIT） | 带 playwright（仅登录用）和 curl_cffi（上传必须）；`--browser chrome` 用系统 Chrome，无需下载浏览器 |
| 画面处理 | `opencv-python-headless`、`numpy`、`scenedetect` | 见下 |
| PDF | `reportlab`（生成）、`pillow`（读图片尺寸）、`pypdf`（校验） | 见下 |
| 命令行 | `typer` | |

没有 Whisper / OCR / 大模型依赖。安装体积约 400 MB（OpenCV、Playwright 占大头）。

## convert 怎么把视频变成"关键帧 PDF"

1. **下载**（yt-dlp）：只取**音频流**（m4a/AAC）和**≤720p 的纯视频流**（优先 H.264，仅用于取帧，不合并、不保存整段视频）。
2. **音频**（ffprobe/ffmpeg）：AAC/MP3 **原样保留，不重新压缩**；校验时长（防截断）和可解码。只有格式不对，或超过 NotebookLM 200 MB 上限时才转码（超限时用 96 kbps，**绝不截断**）；仍超限则明确报错。
3. **选帧**（OpenCV，默认 `auto` 模式）：每 2 秒（超 30 分钟为 3 秒）顺序解码一帧，缩成 160×90 灰度缩略图，**忽略画面底部 14%（烧录字幕区）**：
   - 画面连续稳定（变化 ≤1%）才算"一页稳定的幻灯片"；与当前页相比变化 >4% 视为新页，并保留该页最完整的稳定状态；
   - 过滤全黑/纯色帧；与已保留帧相似（≤2%）的视为重复；
   - 若检测到的页数太少（< 视频分钟数/2，至少 3），补充定时取帧并**给出 warning**，不会悄悄当成"没有画面"；
   - 最多 150 张，超过则均匀抽稀。
   - **PySceneDetect 只在 `--frames scene` 模式使用**（AdaptiveDetector）；失败时自动退回定时取帧并给 warning。
4. **出图**：对选中的时间点，用 OpenCV 从视频里取**原分辨率整帧**（通常 1280×720），存 JPEG（质量 88）。
5. **成 PDF**（ReportLab）：封面（标题、UP 主、原视频链接、时长、截图数；中文用**嵌入的字体子集**）+ 每页一张截图，页首带**可点击的时间戳**（跳回 B 站对应时间）和书签；JPEG 原样嵌入，不二次缩放压缩。随后用 pypdf 重新打开校验页数、大小和封面文字。

## 体积怎么控制

- **音频**：不主动压缩，大小就是 B 站音频流本身（实测约 130–320 kbps：6 分钟 6.1 MB、20 分钟 47 MB、38 分钟 37 MB）。上限 200 MB，超过才降码率转码。
- **PDF**：我们选择**可读性优先，不为缩小体积降画质**。体积 ≈ 截图数 × 约 140–240 KB（实测 14 张 2.3 MB、42 张 10 MB）。能控制体积的只有这几项：页数上限 150、相似页去重、取帧间隔、只取 ≤720p 视频流；最坏情况约 150 × 240 KB ≈ 36 MB，远低于 200 MB 上限。
- 上传用 2 MB 分块断点续传，大文件在不稳定网络下也能传完。

## 失败后怎么办

任务状态保存在你的工作目录里（默认见上文，文件名 `jobs.sqlite`；用 `bili2notebooklm setup` 或环境变量 `BILI2NB_HOME` 可以改位置）。

```bash
bili2notebooklm jobs                 # 查看历史任务
bili2notebooklm status <job_id>      # 查看某个任务
bili2notebooklm retry <job_id>       # 从上次检查点继续，不会重复建本/重复上传
```

对限流与网络错误，工具会最多自动尝试 5 次（指数退避），仍失败则保留任务，等你 `retry`。Ctrl-C 会把任务标成中断，同样可 `retry`。

### 登录失效（AUTH_REQUIRED，退出码 20）

`convert` 不受影响；`import` 会在下载前先检查登录并报 `AUTH_REQUIRED`。修复：

```bash
bili2notebooklm auth login --browser chrome
bili2notebooklm retry <job_id>       # 或直接重新 import
```

### 退出码

| 码 | 含义 | 码 | 含义 |
|---|---|---|---|
| 10 | URL 无效 | 21 | 创建笔记本失败 |
| 11 | 视频不可用 | 22 | 上传失败 |
| 12 | 媒体处理失败 | 23 | 来源未能 READY |
| 13 | 缺少 ffmpeg 等工具 | 24 | 被限流 |
| 14 | 磁盘空间不足 | 25 | 网络错误 |
| 20 | 需要重新登录 | 26 | SDK 版本/接口不兼容（已在任何云端写入前安全停止，见 [SDK 私有接口清单](docs/SDK_PRIVATE_API.md)） |
| 30 | 找不到任务 | 130 | 被用户中断 |
| 1 | 内部错误 | | |

## 凭证与隐私

- **Google 登录**：由你本人在浏览器里完成，不需要、也不接受手工粘贴 Cookie。登录状态由 `notebooklm-py` 保存在 `~/.notebooklm/profiles/<profile>/storage_state.json`（Windows：`%USERPROFILE%\.notebooklm\...`；可用环境变量 `NOTEBOOKLM_HOME` 改位置）。这个文件相当于你的登录凭证：**本工具不读取其内容、不打印、不复制**，任务库和 `--json` 输出里也没有 Cookie。
- **B 站**：只处理公开视频，下载不使用任何登录信息。
- **登录失效**：`import` 会在下载之前报 `AUTH_REQUIRED`（退出码 20），`convert` 不受影响。重新执行 `bili2nb setup`（或 `bili2nb auth login`）登录即可，然后 `bili2nb retry <job_id>`。
- **绝不要提交或分享**：`~/.notebooklm/`、`storage_state*.json`、`*cookies*`、`.env*`、`jobs.sqlite`、工作目录、下载的音视频、生成的 PDF、日志、含真实笔记本链接的截图。`.gitignore` 已覆盖并有自动化测试。
- 详见 [SECURITY.md](SECURITY.md)（漏洞报告、凭证保护、非官方 API 风险）。

## 已知限制

- NotebookLM 单个上传文件上限 200 MB；音频超过上限时会明确报错。
- 上传大一点的文件时，最后一步可能被断开连接，**服务端往往已经收下**。工具会等待并核对云端，不会盲目重传；失败遗留的、卡住的空壳来源（仅限本工具自己上传、同名、状态 PREPARING/ERROR）会被自动清理，READY 来源与其他文件绝不删除。
- 笔记本由本工具创建后不会自动删除；需要删除请在 NotebookLM 网页操作。
- 关键帧选取是启发式的（幻灯片稳定检测，失败时退回定时取帧并给出 warning），不保证覆盖所有视频类型。
- 非 V1 范围：远程控制、Bot、图形界面、浏览器插件、本地转录/OCR。

## 开发

```bash
uv run pytest -q      # 默认全部离线
uv run ruff check src tests
```

测试默认不联网（socket 被禁用）；真实 NotebookLM 验证需显式 `--live` 并经用户同意。验收记录见 `docs/TEST_REPORT.md`，环境与 SDK 事实见 `docs/ENVIRONMENT.md`，开发过程见 `docs/DEV_LOG.md`。

## 许可证

[MIT](LICENSE) © 2026 Ziwei Zhao。

本工具与 Google、哔哩哔哩无关；NotebookLM 部分依赖非官方库，请自行评估使用风险并遵守相关服务条款。
