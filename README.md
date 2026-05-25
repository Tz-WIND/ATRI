# ATRI

ATRI 是一个本地优先的 **AI Agent 框架 / 原生音乐工作站**。它把可执行的 Agent Runtime、Vue Dashboard、实时 Rust Audio Host、DAW 工程模型、音乐库与插件工作流放在同一个应用里。

项目仍在快速迭代中，当前目标是让 Agent 不只停留在对话层，而是能够读取工程状态、操作文件、调用工具、写入 MIDI、控制播放，并和 Dashboard / Audio Host 共享同一份本地状态。

## 核心能力

- **Agent Runtime**：支持 OpenAI 兼容接口和 Anthropic Messages API、工具调用、会话持久化、Plan / Agent 模式、子任务调度、MCP、Skills、Web 搜索和文件工作区。
- **Dashboard**：基于 Quart + Vue，提供 Chat、Studio、Music、Workspace、Knowledge、MCP、Skills、Adapters、Settings 等页面。
- **AI DAW / Studio**：支持轨道、MIDI clip、audio clip 占位、piano roll、tempo / meter、controller lane、automation、mixer、插件 rack、transport 和工程持久化。
- **Rust Audio Host**：基于 CPAL 的本地实时音频 Host，支持内置 Basic Synth、VST3 扫描 / 加载、VST2 扫描信息、插件 state、原生插件编辑器窗口和音频设备配置。
- **音乐库与播放器**：扫描本地音乐目录，读取元数据、封面、歌词，支持搜索、队列、播放控制和全屏播放器。
- **Knowledge**：本地知识库导入、切分、embedding、rerank、检索和 Chat 上下文注入。
- **平台接入**：内置 WebChat 和 OneBot11 适配，可用于 Dashboard 对话或 QQ / Napcat 等反向 WebSocket 场景。

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

## Agent 与音乐工程

ATRI 的 Agent 可以通过工具直接操作本地工程：

- `midi_query` / `midi_inspect`：查看工程、轨道、clip、note、controller/event 和 velocity 统计。
- `midi_write`：向指定轨道生成或覆盖 MIDI notes。
- `midi_diff`：按 add / delete / update 精确修改 notes 和 MIDI events。
- `midi_batch_edit`：批量编辑 velocity、CC、expression、modulation、pitch bend、aftertouch 等曲线。
- `music_player`：搜索音乐、播放、暂停、切歌和调整音量。
- 文件与代码工具：读取、写入、编辑、搜索、终端命令、任务调度和 Web 搜索。

MIDI 工具默认使用工程时间线上的 absolute beat。只有显式传入 `local_start` 时，时间才表示 clip-local beat。

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
