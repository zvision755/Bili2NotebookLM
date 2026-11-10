# bili2nb

[![CI](https://github.com/zvision755/Bili2NotebookLM/actions/workflows/ci.yml/badge.svg)](https://github.com/zvision755/Bili2NotebookLM/actions/workflows/ci.yml)

把一个公开的 B 站视频链接，一步导入你自己的 NotebookLM：

```
bili2notebooklm <B站链接 或 BV号>
```

CLI 会自动下载音频、抽取关键画面生成 PDF、**新建一个笔记本**、上传来源、等待 NotebookLM 处理完成（READY），最后打印笔记本链接。**多 P 视频默认处理全部分 P**：每个分 P 各自生成音频和 PDF，但所有分 P 只进**同一个**笔记本。转录和总结都由 NotebookLM 自己完成，本地不跑 Whisper、不调用任何 LLM。

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

三个系统的登录方式一致：默认使用 Playwright 管理的 **Chromium**，**不需要**预先装 Chrome 或 Edge（首次登录会下载约 170 MB，只下载一次）。想用已装的浏览器就 `bili2nb setup --browser chrome`（或 `msedge`）。工作目录默认是 `%APPDATA%\Bili2NotebookLM`。

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

登录默认使用 Playwright 管理的 **Chromium**，不需要装 Chrome（首次登录会下载约 170 MB，只下载一次）；想用已装的 Chrome 就 `bili2nb setup --browser chrome`。工作目录默认是 `~/Library/Application Support/Bili2NotebookLM`。

### Linux（Debian / Ubuntu 为例）

```bash
sudo apt update && sudo apt install -y git curl ffmpeg fonts-wqy-microhei   # fonts-wqy-microhei：让 PDF 封面中文能嵌入字体
curl -LsSf https://astral.sh/uv/install.sh | sh      # 安装 uv，装完重新打开终端
git clone https://github.com/zvision755/Bili2NotebookLM.git
cd Bili2NotebookLM
uv tool install . --overrides overrides.txt
uv tool update-shell                                  # 然后重新打开终端
bili2nb doctor
bili2nb setup                                         # 登录 Google 需要图形桌面环境（默认自动下载 Chromium，不需要装 Chrome）
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
bili2notebooklm BV1VV4y1V7Pz?p=3                                # 只处理第 3 个分 P（也可 --part 3；还可 --part 8-27 选一段）
bili2notebooklm --json --keep-files BV1VV4y1V7Pz                # 选项放前面也行
```

直接跟链接/BV 号就等于 `import`：下载音频、生成关键帧 PDF、**新建一个笔记本**、上传、等待 READY，最后打印笔记本链接。同一视频再次执行会直接返回原笔记本，不会重复创建。

### 多 P 视频

```bash
bili2nb BV1xxxxxxxxx            # 没有指定 P：处理这个视频的全部分 P，只建 1 个笔记本
bili2nb "BV1xxxxxxxxx?p=3"      # 只处理 P3（链接里的 p 优先于 --part）
bili2nb BV1xxxxxxxxx --part 3   # 同上
bili2nb BV1xxxxxxxxx --part 8-27     # 只处理 P8 到 P27（共 20 个分 P），仍然只建 1 个笔记本
bili2nb BV1xxxxxxxxx --part 1,3,5    # 只处理 P1、P3、P5
bili2nb BV1xxxxxxxxx --part 1-3,7    # 混合写法：P1、P2、P3、P7
bili2nb BV1xxxxxxxxx --audio-only   # 每个分 P 只上传音频
```

`--part` 的写法：单个数字、范围 `a-b`、逗号列表，或它们的组合；重复的会去掉，并按升序处理。`0`、负数、倒序范围（`5-3`）、空项（`1,,2`）、字母等写法会直接报错（退出码 10，`INVALID_URL`），不会碰网络。链接里的 `?p=N` 始终优先于 `--part`。`convert` 与 `import` 用法一致。

- 选了多个、但不是全部分 P：任务以所选集合为单位记忆（同一选择再次导入会复用原笔记本；换一个选择就是另一个任务、另一个笔记本）。只选 1 个分 P 与以前的 `--part N` 完全一样。选中了全部分 P 等同于不写 `--part`。
- 来源标题里的 `P08｜…` 始终是视频里真实的分 P 序号；超额合并时，不连续的选择会写成 `P01+P03+P05` 这样的真实成员，而不是假装连续。

- 一个 3 P 的视频会得到 1 个笔记本、6 个来源：`P01｜第一集｜音频`、`P01｜第一集｜关键帧PDF`、`P02｜…`。某个分 P 没有有效画面时，只上传它的音频并给出提示。
- **先全部转换，再建笔记本**：所有分 P 都在本地转换、校验完、上传计划确认可行之后，才会在 NotebookLM 里创建笔记本。某个分 P 下载失败时云端什么都不会创建，已完成的分 P 下次直接复用（`bili2nb retry <job_id>`）。
- 中断（Ctrl+C）或上传中途失败后 `retry` 只继续没完成的来源，不会重复建笔记本、不会重传已成功的来源。
- 无法确定总分 P 数（B 站接口不可用）时，不会悄悄只处理 P1，而是报错并提示用 `--part` 指定（如 `--part 3` 或 `--part 1-5`）。
- 每个分 P 的产物独立保存在 `<输出目录>/<BV号>/P01_标题/`，顶层 `manifest.json` 记录全部分 P 的状态；`--force-new` 只会新建一个笔记本。

### 笔记本来源数量限制

NotebookLM 每个笔记本有来源数量上限（免费账号目前是 50 个，付费等级更高）、单文件上限 200 MB。工具从你的账号**读取实际上限**（读不到时按 50 保守处理，并给出提示），然后：

1. 放得下：每个分 P 各传音频和 PDF（默认，也是网络不稳时最稳妥的方式：断一次只损失一个小文件）。
2. 放不下：**只合并到刚好不超限**，并且音频和 PDF **对齐**。把连续的分 P 切成 g 个大小均衡（相差不超过 1）的组；每一组的音频合并成 1 个来源、PDF 合并成 1 个来源，两者覆盖完全相同的分 P（“第 X 组 = P01–P05 的音频和 PDF”）；只有 1 个分 P 的组仍是原来的逐 P 来源。g 取**让来源总数不超限的最大值**，所以合并得最少。单个合并文件 ≤ 190 MiB：某组的合并音频或 PDF 超过它，就把这一组（音频和 PDF 一起）对半拆开。合并 PDF 带每个分 P 的书签，开头有目录页，列出各分 P 在合并音频里的起始时间。逐 P 的原文件仍保留在本地。例子（账号上限 50）：
   - 30 个分 P（逐 P 要 60 个来源）：g=25，5 组两个分 P、20 组单个分 P，共 25 个音频 + 25 个 PDF = 50 个来源。
   - 132 个分 P：g=25，7 组 6 个分 P、18 组 5 个分 P，共 50 个来源。
   - 30 个分 P、上限 31：g=16 要 32 个来源，所以取 g=15（每组 2 个分 P），共 30 个来源。
   - 上限 2：只有 1 组（1 个音频 + 1 个 PDF），前提是它们都 ≤ 190 MiB。
3. 合并到体积上限允许的最少来源数后仍超限：**在创建笔记本之前**报错（退出码 27），写明需要多少来源、上限是多少，建议用 `--part`（如 `--part 1-20`）缩小范围。**绝不会**自动拆成多个笔记本。

> 为什么不一口气合并成最少：真实试验里，一个 122 MB 的合并音频在这台机器到 Google 的网络上连续 4 次传输中途断开、22 分钟都没传完；小文件断一次损失很小。所以默认让文件尽量小。

**模拟小额度：`--source-limit N`**（`import` 专用，N ≥ 2）。按“账号最多 N 个来源”来规划，用来在付费账号上预演免费账号（50 个来源）的合并行为：

```bash
bili2nb import BV1xxxxxxxxx --source-limit 50
```

实际生效的上限是 `min(账号读到的上限, N)`，只会更严、不会放宽；生效时 `--json` 的 `upload_plan.limit_origin` 为 `override`、`source_limit` 为有效值。它不改变任务键：同一选择已有任务时沿用原计划并提示，要按新上限重做请加 `--force-new`。

**可选：`--merge-sources`**（只对一次选中 ≥ 2 个分 P 的 `import` 有意义，`convert` 没有）。来源数量放得下时默认仍是逐 P 上传，放不下时也只做最小合并；加上 `--merge-sources` 则无论是否超额，都把连续分 P 合并成**尽量少**的来源（音频、PDF 各自独立按单文件 ≤ 190 MiB 贪心分组，标题如 `P08-P27｜音频`，合并 PDF 的目录页列出各分 P 在合并音频里的起始时间）。

```bash
bili2nb BV1xxxxxxxxx --part 8-27 --merge-sources
```

- 它**不改变任务键**：同一视频、同一选择已有任务（不论是逐 P 还是合并导入的）就直接复用，不会重做；想按合并方式重新导入，请加 `--force-new`（会新建 1 个笔记本）。
- 选中的分 P 只有 1 个时，开关被忽略并给一行提示。
- 取舍：来源数少（上传的固定开销小、断点续传检查点少），但**在网络不稳时大文件上传失败的代价更大，可能反而更慢**——失败时重传的是整个大文件，且在 NotebookLM 里引用只会指向“合并音频”，要靠 PDF 目录页的时间点对应到具体分 P。是否更快见 [docs/V1.2_MERGE_SOURCES.md](docs/V1.2_MERGE_SOURCES.md)。

### 第一次使用：设置工作目录和 Google 登录

第一次在终端里运行时会自动引导（也可以随时手动执行 `bili2notebooklm setup`）：

1. **工作目录**：存放任务记录和临时文件。回车使用默认位置，也可以输入别的路径：
   - macOS 默认：`~/Library/Application Support/Bili2NotebookLM`
   - Windows 默认：`%APPDATA%\Bili2NotebookLM`
   - 想改位置：再运行 `bili2notebooklm setup`，或直接 `bili2notebooklm setup --work-dir D:\bili2nb`；也可用环境变量 `BILI2NB_HOME`（优先级最高）。
2. **Google 登录**：弹出浏览器，你自己登录 Google；工具只保存登录状态，不经过本工具、不进日志。之后无需重复登录（登录失效时会再提示）。不需要手工复制 Cookie，B 站公开视频也不需要 B 站 Cookie。
   - 默认浏览器是 Playwright 管理的 **Chromium**（三个系统一致），不需要预装 Chrome 或 Edge。**首次登录会先下载约 170 MB 的浏览器组件**（显示进度，只下载一次，存放在 Playwright 的用户级缓存目录，不会改动你已有的 Chrome / Edge / Safari 及其设置）。
   - 想用已装的浏览器：`bili2nb auth login --browser chrome`（或 `msedge`）。
   - **下载失败**（网络或代理问题）：直接重新运行 `bili2nb auth login` 即可重试；需要代理时先设置 `HTTPS_PROXY`；或改用 `--browser chrome|msedge`。
   - **浏览器没能启动 / 没在 5 分钟内登录完**：同样重新运行 `bili2nb auth login`；交互终端里，Chromium 失败且检测到本机有 Chrome 或 Edge 时，会询问是否改用它重试。
   - Linux 需要图形桌面环境；无桌面的服务器请在有桌面的机器上登录一次。
   - 暂不支持读取你已有浏览器里的 Google 登录状态（`--browser-cookies`）：它会读取浏览器里的全部 Google Cookie，隐私面更大，且各平台的 Cookie 加密/权限问题较多，见 [V1.2 设计](docs/V1.2_DESIGN.md)。

非终端环境（脚本/`--json`）不会弹任何提问，缺登录时返回错误码 `AUTH_REQUIRED`。

### 只做本地转换（不需要登录 Google）

```bash
bili2notebooklm convert BV1VV4y1V7Pz --output ./out      # 产物：audio.m4a、keyframes.pdf、manifest.json
```

`convert` 与 `import` 一样：没指定 P 就处理全部分 P（每个分 P 一个子目录）。其他选项：`--part N|a-b|a,b,c`、`--frames auto|slides|scene|none`、`--audio-only`、`--keep-files`、`--force-new`（为同一视频另建一个笔记本）；仅 `import`：`--merge-sources`（见上）。

> Windows 说明：默认目录、ffmpeg 提示和登录流程已按 Windows 适配，但本项目目前只在 macOS 上做过真实验证；Windows 上若遇到问题请反馈。

## 依赖

| 类别 | 依赖 | 说明 |
|---|---|---|
| Python | **≥ 3.11** | 完整测试在 CI 的 3.11 与 3.12 上通过（Ubuntu、Windows；macOS 为 3.12）；`uv tool install` 默认会选 3.14，Ubuntu 的 README 安装块实测以 3.14.4 安装并通过 `doctor` 与 smoke，开发机另用 3.14.7 跑过完整测试；3.13 / 3.15 未实测 |
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
| 27 | 来源数量/大小超出账号上限（即使合并分 P 也装不下；**未创建任何笔记本**） | 30 | 找不到任务 |
| 28 | 该视频的分 P 与已有任务记录不一致（用 `--force-new` 重新导入） | 130 | 被用户中断 |
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
