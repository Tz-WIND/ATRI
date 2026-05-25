# ATRI

**让 LLM 直接写 DAW，而不是让模型吐出一段音频。**

ATRI 是一个本地优先的 **AI Agent 原生音乐工作站**。它尝试的是一条和「输入提示词，生成一段完整音频」完全不同的 AI 音乐路线：让 LLM 进入 DAW 的工程内部，读取时间线、轨道、clip、和声标记、MIDI notes、controller events 与自动化数据，然后把自己的判断写回底层音乐结构。

换句话说，ATRI 不把 AI 音乐创作理解为一次性吐出一段不可编辑的波形。它把音乐工程本身变成 Agent 可以操作的界面：AI 可以写 bassline、画 CC 曲线、人性化 timing 和 velocity、补和声、铺 pad、做变奏、整理工程、解释编曲思路，也可以只在你需要的时候完成一个很小但很费手的编辑动作。

最终留下来的不是一个黑箱音频成品，而是 MIDI、automation、工程文件、和声轨、控制器曲线和插件链。创作者可以继续播放、检查、撤销、重写、局部修改，像处理任何正常 DAW 工程一样处理 AI 的输出。

这使 ATRI 更接近一种 **LLM-driven DAW workflow**：AI 不替代工作流，而是嵌入工作流；不绕开创作者，而是和创作者共享同一份可编辑工程状态。

**如果这个项目对你有帮助，或者你觉得这个思路值得探索，欢迎点亮 Star。**

## 项目命题

大多数 AI 音乐产品把模型放在工作流之外：你描述一首歌，模型生成音频，你再决定要不要接受它。这个过程很直接，但也很难继续编辑。你无法精确地要求「把第二小节的低音提前一点」「只把弦乐的 CC11 画成渐强」「把这个和弦保留但换一个更克制的 voicing」，因为模型输出的是最终声音，而不是工程内部的可控结构。

ATRI 的设计反过来：

- **以 DAW 工程为生成目标**：让 AI 写入 MIDI notes、controller events、tempo / meter、和声轨、自动化和插件状态。
- **以 LLM 做音乐工程推理**：利用语言模型对结构、意图、乐理、上下文和用户反馈的理解能力，而不是直接生成波形。
- **以可编辑数据承接结果**：每一次生成都落在可检查、可回滚、可继续手工编辑的工程数据里。
- **以协作而非替代为边界**：AI 可以完成整段音乐草稿，也可以只辅助画一条 CC、修一段 velocity、给出编曲思路或检查钢琴可演奏性。

这里的核心不是「让 AI 一次做完音乐」，而是让 AI 成为 DAW 里的执行者、助理和共同编辑者。

## 技术亮点

- **Agent 直接操作 DAW 底层数据**
  `midi_write`、`midi_diff`、`midi_batch_edit`、`studio_piano_lane_write` 等工具让 Agent 不只是聊天，而是能真实写入工程结构。它可以追加 notes、替换片段、精确编辑单个事件、批量生成人性化曲线，也可以先写和声轨再按和声生成旋律与伴奏。

- **Symbolic first，而不是 waveform first**
  ATRI 优先生成可编辑的音乐符号和工程控制数据。MIDI、CC、pitch bend、aftertouch、tempo、meter、automation 都保留为结构化数据，方便用户继续调整，也方便下一轮 Agent 读取和理解。

- **Dashboard / Agent / Audio Host 共享同一工程源**
  Python Runtime 持有工程模型，Vue Dashboard 负责可视化编辑，Rust Audio Host 负责实时播放和插件渲染。三者围绕 `data/music_workstation/project.json` 协作，而不是各自维护一份互相漂移的状态。

- **实时 Rust Audio Host**
  `atri-host` 使用 CPAL 输出音频，支持 transport 同步、内置 Basic Synth、VST3 扫描与加载、插件 state、原生插件编辑器窗口和音频设备配置。AI 写入的 MIDI 可以直接在本地 Host 中播放。

- **面向音乐制作的 Agent 工具链**
  除了常规文件、终端、搜索、MCP、Skills 等 coding-agent 能力，ATRI 还提供音乐专用工具：MIDI 查询、分页 inspect、精确 diff、批量 controller 编辑、音乐库播放控制、钢琴可演奏性检查、piano lane 写入等。

- **可扩展的本地工作台**
  Dashboard 包含 Chat、Studio、Music、Workspace、Knowledge、MCP、Skills、Adapters、Settings。它既是 AI 对话入口，也是 DAW、音乐库、知识库和工具管理界面。

## 设计哲学

### 1. AI 应该进入工程，而不是停在工程外

如果 AI 只能给建议，它就像站在屏幕外的顾问；如果 AI 能读取和写入工程，它才开始成为工作流的一部分。ATRI 的 Agent 可以看到工程状态、调用工具、修改文件、写 MIDI、同步 Host，并把结果反馈到可视化 Studio 中。

### 2. 生成结果必须可编辑

音乐制作不是一次性选择题。一个可继续编辑的 MIDI clip，比一段听起来不错但难以拆开的音频更适合真实工作流。ATRI 把 AI 输出放在 notes、events、automation、lane markers 这些可修改对象上，让用户可以继续接管。

### 3. LLM 适合做结构化音乐工程

LLM 不擅长直接发声，但擅长理解意图、约束、上下文、风格描述、乐理关系和操作步骤。ATRI 让 LLM 做它擅长的部分：规划、推理、写结构化数据、按反馈迭代，而声音由 DAW 工程和音频 Host 产生。

### 4. AI 是协作者，不是最终裁判

ATRI 可以生成完整段落，但它更重要的价值是嵌入细碎的制作过程：帮你画 CC1/CC11、微调 velocity、人性化鼓组、补一个 passing chord、解释为什么某个 voicing 浑浊、把一段旋律变奏成 call-and-response。它服务的是创作过程本身。

### 5. 本地优先，状态透明

工程、会话、运行事件、音乐库缓存和工具输出默认保存在本地 `data/`。这让 ATRI 更像一套可以被检查和改造的工作站，而不是只能通过远端黑箱交互的服务。

## 核心能力

- **Agent Runtime**：支持 OpenAI 兼容接口和 Anthropic Messages API、工具调用、会话持久化、Plan / Agent 模式、子任务调度、MCP、Skills、Web 搜索和文件工作区。
- **AI DAW / Studio**：支持轨道、MIDI clip、audio clip 占位、piano roll、tempo / meter、controller lane、automation、mixer、插件 rack、transport 和工程持久化。
- **Rust Audio Host**：基于 CPAL 的本地实时音频 Host，支持内置 Basic Synth、VST3 扫描 / 加载、VST2 扫描信息、插件 state、原生插件编辑器窗口和音频设备配置。
- **音乐库与播放器**：扫描本地音乐目录，读取元数据、封面、歌词，支持搜索、队列、播放控制和全屏播放器。
- **Knowledge**：本地知识库导入、切分、embedding、rerank、检索和 Chat 上下文注入。
- **平台接入**：内置 WebChat 和 OneBot11 适配，可用于 Dashboard 对话或 QQ / Napcat 等反向 WebSocket 场景。

## Agent 与音乐工程

ATRI 的 Agent 可以通过工具直接操作本地工程：

- `studio_piano_lane_write` / `studio_piano_lane_diff`：写入或编辑 piano roll 的拍号轨、和声轨等结构性标记。
- `midi_query` / `midi_inspect`：查看工程、轨道、clip、note、controller/event 和 velocity 统计。
- `midi_write`：向指定轨道生成或覆盖 MIDI notes。
- `midi_diff`：按 add / delete / update 精确修改 notes 和 MIDI events。
- `midi_batch_edit`：批量编辑 velocity、CC、expression、modulation、pitch bend、aftertouch 等曲线。
- `piano_playability_check`：检查钢琴 MIDI 的跨度、密度、跳进、手位交叉和可演奏性风险。
- `music_player`：搜索音乐、播放、暂停、切歌和调整音量。
- 文件与代码工具：读取、写入、编辑、搜索、终端命令、任务调度和 Web 搜索。

典型工作流不是「生成一首歌然后结束」，而是连续迭代：

```text
用户：给这个 8 小节段落写一个 C minor synthwave bassline。
Agent：先读取当前工程和 tempo，写入 bass MIDI，并同步到 Studio。

用户：第二遍更紧一点，velocity 做一点 humanize。
Agent：用 midi_diff 调整节奏位置，用 midi_batch_edit 处理 velocity。

用户：副歌前两小节画一个 CC11 渐强，和声标出来。
Agent：写入 expression controller curve，并在 harmony lane 添加标记。
```

MIDI 工具默认使用工程时间线上的 absolute beat。只有显式传入 `local_start` 时，时间才表示 clip-local beat。

## Dashboard 页面

| 页面 | 用途 |
| --- | --- |
| Chat | Agent 对话、工具卡片、thinking block、会话切换、文件上下文 |
| Studio | DAW 工程编辑、MIDI / audio clip、插件、自动化和 Rust Host 控制 |
| Music | 本地音乐库、搜索、播放队列、歌词、封面和播放器 |
| Knowledge | 知识库、文档导入、切分、检索和上下文注入 |
| Workspace | Agent 文件工作区浏览和编辑 |
| Adapters | WebChat / OneBot11 接入配置 |
| MCP | MCP server 管理、校验和热加载 |
| Skills | 本地 `SKILL.md` 查看、编辑、导入和下载 |
| Settings | Provider、模型池、生成参数、音频、音乐目录和 Agent 行为 |

## 快速开始

命令示例以 PowerShell 为主。

### 环境要求

- Python 3.11+
- uv
- Node.js 18+
- Rust stable + Cargo
- Windows 构建 `atri-host` 时建议安装 Visual Studio Build Tools，勾选 C++ 桌面开发和 Windows SDK

### 安装与构建

```powershell
git clone <repo-url>
cd ATRI

uv sync --group dev
Copy-Item config.yaml.example config.yaml
```

编辑 `config.yaml`，至少配置可用的模型 Provider。可以直接填 `api_key`、`base_url`、`model`，也可以启动后在 Dashboard 的 `Settings -> Providers` 中维护。

```powershell
Push-Location frontend
npm ci
npm run build
Pop-Location

Push-Location atri-host
cargo build -p atri-host
Pop-Location

uv run python main.py
```

启动后打开：

```text
http://localhost:6185
```

首次访问如果没有可用密码，会进入 Dashboard 账号初始化流程。

## 常用配置

主要配置文件是 `config.yaml`，可从 `config.yaml.example` 复制生成。

| 配置项 | 说明 |
| --- | --- |
| `providers` / `active_models` | 聊天模型 Provider 与可用模型池 |
| `embedding_model` / `rerank_model` | Knowledge 检索使用的 embedding 和 rerank 模型 |
| `image_transcription` | 图片输入转录模型，用于把截图、谱面、错误图等转成文本上下文 |
| `novelai` | NovelAI 图片生成工具配置 |
| `dashboard` | Web UI 开关、监听地址、端口和账号 |
| `audio_host` | Rust Audio Host 路径、采样率、缓冲区、音频后端和位深 |
| `vst3_plugin_paths` / `vst2_plugin_paths` | 额外插件扫描目录 |
| `music_directories` | 本地音乐库扫描目录 |
| `onebot11` | OneBot11 反向 WebSocket、白名单和群聊上下文 |
| `mcp_servers` | MCP server 配置 |
| `skills_root` / `skills` | 本地 Skills 根目录和启用配置 |

## Audio Host 与 ASIO

普通构建：

```powershell
Push-Location atri-host
cargo build -p atri-host
Pop-Location
```

启用 ASIO：

```powershell
Push-Location atri-host
cargo build -p atri-host --features asio
Pop-Location
```

ASIO 构建前需要：

1. 安装 Visual Studio Build Tools，包含 C++ 桌面开发和 Windows SDK。
2. 安装 LLVM / Clang，并确保 `libclang` 可被找到。
3. 下载并解压 Steinberg ASIO SDK。
4. 设置环境变量：

```powershell
setx CPAL_ASIO_DIR "你的ASIOSDK放置路径"
setx LIBCLANG_PATH "C:\Program Files\LLVM\bin"
```

重新打开终端后再构建。缺少依赖时，`asio-sys` 会在编译阶段报错。

`audio_host.audio_engine` 支持：

- `default`：系统默认输出设备。
- `<host>`：指定 CPAL host 的默认设备，例如 `wasapi`。
- `<host>::<device name>`：指定具体设备，例如 `wasapi::Speakers (Realtek USB Audio)`。

`audio_host.bit_depth` 支持 `f32`、`i16`、`i24`。其中 `i24` 会优先匹配 CPAL 暴露的 `I32` / `U32` 等设备格式，实际可用性取决于声卡驱动。

## 架构概览

```text
Vue Dashboard
  Chat / Studio / Music / Knowledge / Settings
        |
        | REST + WebSocket
        v
Quart Dashboard
  routes / auth / settings / music / knowledge / mcp / skills
        |
        v
Python Runtime
  Agent / tools / pipeline / platform adapters / persistence
        |
        | JSON IPC + process management
        v
Rust Audio Host
  CPAL / MIDI / VST / DSP / plugin editors
        |
        v
Audio devices
```

核心数据默认写入 `data/`：

- `data/sessions/`：会话历史。
- `data/runtime/`：运行时事件、timeline 和任务状态。
- `data/tool_outputs/`：压缩溢出的工具输出。
- `data/music_workstation/project.json`：Studio / Agent / Host 共享的 DAW 工程。
- `data/music_cache/`：音乐库扫描缓存。

## 开发命令

Python：

```powershell
uv run ruff check .
uv run mypy core/ dashboard/
uv run pytest --tb=short -q
```

前端：

```powershell
Push-Location frontend
npm run lint
npm run build
npm run dev
Pop-Location
```

Rust：

```powershell
Push-Location atri-host
cargo fmt
cargo test
cargo build -p atri-host
Pop-Location
```

Makefile 也提供了常用目标：

```powershell
make install
make lint
make typecheck
make test
make frontend-build
make ci
```

## 项目结构

```text
ATRI/
├── main.py                    # Python 入口
├── config.yaml.example        # 配置示例
├── core/
│   ├── agent/                 # Agent 主循环、LLM、上下文和模式
│   ├── tools/                 # 文件、终端、MIDI、音乐、MCP、Skills 等工具
│   ├── pipeline/              # 平台消息处理流水线
│   ├── platform/              # WebChat / OneBot11 适配
│   ├── knowledge/             # 文档切分、embedding、rerank、检索和存储
│   ├── runtime/               # timeline、task store、todos
│   ├── plugin/                # Python 插件系统
│   ├── host.py                # Rust Audio Host 进程管理
│   └── music_project.py       # DAW 工程 JSON 模型
├── dashboard/
│   ├── server.py              # Quart Dashboard
│   ├── music.py               # Music / Studio / Host API
│   └── routes/                # Auth、Chat、Settings、MCP、Skills、Knowledge 等路由
├── frontend/
│   └── src/
│       ├── components/chat/   # Chat UI
│       ├── components/music/  # Music、Player、Studio
│       ├── components/pages/  # Workspace、Knowledge、Adapters、MCP、Skills
│       ├── components/settings/
│       └── composables/       # API、Auth、Chat、Music、DawHost、WebSocket
├── atri-host/
│   ├── atri-core/             # 音频 buffer、MIDI、tempo / time 类型
│   ├── atri-engine/           # Session、route、transport、synth、processor
│   ├── atri-host-bin/         # CPAL driver、IPC commands、editor host
│   └── atri-vst3/             # VST scanner、factory、plugin wrapper
├── tests/                     # Python 测试
├── plugins/                   # 用户插件目录
├── skills/                    # Skills 根目录
├── workspace/                 # Agent 文件工作区
└── data/                      # 本地运行数据
```

## License

[GNU Affero General Public License v3.0](LICENSE)
