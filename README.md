# ATRI

ATRI 是一个 **AI Agent框架/原生音乐工作站** —— 将 DAW、实时音频引擎与 Agent 系统融为一体的本地创作环境。

这不是聊天机器人外面套一层 DAW 的壳，也不是在 DAW 里塞一个 AI 对话框。ATRI 的 Agent 可以直接读取工程状态、写入 MIDI、操控播放、管理文件，像一名坐在你旁边看着屏幕的合作伙伴，而不是只能隔空给建议的旁观者。

项目当前仍在快速推进中......

## 快速开始

适合先在本机跑起 Dashboard + Rust Audio Host。命令示例以 PowerShell 为主。

### 环境要求

- Python 3.11+
- uv
- Node.js 18+
- Rust stable + Cargo
- Windows 构建 `atri-host` 建议安装 Visual Studio Build Tools（C++ 桌面开发）

### 启动步骤

```powershell
git clone <repo-url>
cd ATRI

uv sync
Copy-Item config.yaml.example config.yaml
```

编辑 `config.yaml`，至少填入可用的 `api_key`、`base_url` 和 `model`；也可以启动后在 Dashboard 的 `Settings -> Providers` 中配置模型。

```powershell
cd frontend
npm install
npm run build
cd ..

cd atri-host
cargo build -p atri-host
cd ..

uv run python main.py
```

启动后打开：

```text
http://localhost:6185
```

首次访问会进入账号初始化流程。

—————————————————————

如需 Steinberg built-in ASIO 等其他 ASIO 设备：

```powershell
cargo build -p atri-host --features asio
```

前置准备：

1. 安装 **Visual Studio Build Tools**（C++ 桌面开发 + Windows SDK）。
2. 安装 **LLVM/Clang**，确保 `libclang` 可被找到。
3. 下载并解压 **Steinberg ASIO SDK**。
4. 设置环境变量：

```powershell
setx CPAL_ASIO_DIR "你的ASIOSDK放置路径"
setx LIBCLANG_PATH "C:\Program Files\LLVM\bin"
```

## 三大组件

### 1. AI DAW / Studio

面向 MIDI 与插件工作流的 Web Studio，包含时间线、piano roll、轨道列表、Mixer、插件 Rack、Transport 控制和工程持久化。

### 2. Rust Audio Host

本地实时音频引擎，负责 CPAL 输出、transport 同步、MIDI 调度、内置合成器、VST 插件加载与管理、原生插件编辑器及音频设备配置。

### 3. Agent Runtime

本地优先的多模型 Agent 系统，支持工具调用、会话持久化、文件工作区、MCP、Skills、OneBot11/WebChat 接入，以及专为音乐工程设计的 MIDI 操作工具。

## 能力地图

### DAW 与音乐工程

- **Studio 页面** —— 编曲视图、轨道区、时间线、piano roll、inspector 一体化。
- **MIDI Clip 工作流** —— 以 beat 为时间单位，支持 clip、note、controller/event 等完整 MIDI 数据结构。
- **Piano Roll 编辑** —— 选择、绘制、复制、粘贴、删除音符，支持旋律、和弦、bassline、鼓 pattern 等编辑。
- **轨道与 Mixer** —— 独立音量、声像、静音、独奏、颜色、名称，以及乐器/插入式插件槽。
- **插件 Rack** —— 区分 instrument slot 与 insert slot，保存插件路径、元数据与 state chunk。
- **Transport 控制** —— 播放、暂停、停止、跳转、速度、拍号、循环，全部由 Host 端实时执行。
- **工程持久化** —— `data/music_workstation/project.json` 为唯一工程源，Dashboard、Agent、Rust Host 通过同步流程共享同一份状态。
- **Agent 写入工程** —— 通过 `midi_write` / `midi_diff` 直接生成或修改 MIDI，并请求 Dashboard 同步至 Host。

### 实时音频引擎

- **Rust 音频核心** —— `atri-host` 为独立 Rust workspace，含核心类型、引擎、Host 可执行程序和 VST 封装。
- **实时渲染** —— 从工程同步轨道、MIDI 与插件链，按当前 transport 状态渲染音频。
- **内置 Basic Synth** —— 零插件也能播放 MIDI，方便测试与快速草稿。
- **VST3/VST2 扫描** —— 支持系统默认路径与 `config.yaml` 自定义路径。VST3 为主力加载格式，VST2 扫描信息可展示，加载能力持续完善中。
- **插件状态管理** —— 读写插件 state chunk，Host 重启后保留声音状态。
- **原生插件编辑器** —— Host 端管理编辑器窗口，Studio 中可直接打开支持 editor 的插件界面。
- **音频配置** —— 采样率、缓冲区、位深、输出后端/设备均可配，硬件级变更通过重启 Host 生效。

### Agent 与自动化

- **Chat + 工具调用** —— 读写文件、编辑代码、搜索文件、终端命令、Web 搜索、子任务调度。
- **音乐工程工具** —— `midi_write` 生成/覆盖 MIDI，`midi_diff` 原子级精确编辑，`music_player` 控制播放。
- **Plan / Agent 双模式** —— 先讨论方案再动手执行，适合创作类任务。
- **会话持久化** —— 聊天记录与工具结果可保存，Dashboard 支持多会话切换。
- **多模态入口** —— 可配置图像转录模型，将截图、谱面、错误信息转为文本上下文输入主模型。
- **Skills / MCP** —— 本地 `SKILL.md` 技能系统与 MCP server 管理，按需扩展 Agent 能力。

### 音乐库与播放器

- **本地音乐库** —— 扫描 `music_directories`，提取标题、艺术家、专辑、时长、格式、采样率、位深、封面。
- **Music 页面** —— 搜索、全盘播放、格式标签、Hi-Res/Lossless 标识。
- **播放体验** —— 底部迷你播放器 + 全屏播放器，支持队列、歌词、封面、进度、音量与播放模式。
- **Agent 控制播放** —— 搜索音乐、播放指定曲目、暂停、切歌、调节音量。

### Dashboard 工作台

| 页面 | 功能 |
| ------ | ------ |
| Chat | 模型对话、工具卡片、thinking block、会话列表、文件上下文 |
| Studio | DAW 工程编辑、Rust Host 控制 |
| Music | 本地音乐库与播放器 |
| Workspace | 工作区文件浏览与编辑 |
| Adapters | OneBot11 / WebChat 平台接入 |
| MCP | MCP Server 管理 |
| Skills | 本地技能查看、编辑、导入 |
| Settings | Provider、模型、生成参数、视觉转录、Agent 行为、音乐目录、Audio Host |

## 架构

```text
┌──────────────────────────┐
│       Dashboard           │
│   Vue Studio / Chat / UI  │
└─────────────┬────────────┘
              │ REST + WebSocket
┌─────────────▼────────────┐
│     Python Runtime        │
│ Agent / Tools / Project   │
│ Music API / Host Manager  │
└─────────────┬────────────┘
              │ JSON IPC + 进程管理
┌─────────────▼────────────┐
│     Rust Audio Host       │
│ CPAL / MIDI / VST / DSP   │
└─────────────┬────────────┘
              │ 音频设备输出
     Speakers / ASIO / WASAPI
```

Python 侧持有可编辑工程数据，Rust Host 专注实时渲染。三者不争抢同一份状态：Agent 写入 JSON 工程，Dashboard 展示与编辑，Host 接收同步后的轨道与 MIDI 并播放。

## Agent 架构详解

ATRI 的 Agent 层是一套完整的 Coding Agent 框架，拥有LLM + 工具调用的闭环循环，配合多层上下文压缩、子 Agent 并行调度、Plan/Agent 双模式切换，以及可扩展的 MCP/Skills 生态。

不是聊天机器人套一层工具调用，而是一个**以工具执行为核心、以工程产出为导向的自主 Agent 运行时**。

### 整体分层

```text
┌──────────────────────────────────────────────────┐
│                  Platform Layer                   │
│         WebChat (HTTP)  │  OneBot11 (QQ)          │
└──────────────────────┬───────────────────────────┘
                       │ MessageEvent (统一消息模型)
┌──────────────────────▼───────────────────────────┐
│               Pipeline Scheduler                  │
│   WakingCheck → PreProcess → Process → Respond    │
└──────────────────────┬───────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────┐
│                  Agent Core                       │
│  ┌──────────┐ ┌──────────┐ ┌───────────────────┐ │
│  │ LLM 抽象  │ │ 系统提示  │ │ 上下文管理器       │ │
│  │ (OpenAI/ │ │ (动态生成)│ │ (3 层压缩)        │ │
│  │ Anthropic│ │          │ │                   │ │
│  └──────────┘ └──────────┘ └───────────────────┘ │
│  ┌──────────────────────────────────────────────┐ │
│  │              Tool System (~25 tools)          │ │
│  │  bash / edit / write / read / grep / glob    │ │
│  │  agent / midi / music / web_search / mcp    │ │
│  │  skill / mode / task_result / ...           │ │
│  └──────────────────────────────────────────────┘ │
└──────────────────────┬───────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────┐
│              Persistence Layer                    │
│  Sessions │ Runtime Timeline │ Task Store │ Files │
└──────────────────────────────────────────────────┘
```

### Agent 主循环

核心循环位于 `core/agent/agent.py`，是一个标准的 **LLM + 工具调用** 闭环：

```text
用户输入 → 构建上下文 → LLM 推理 ─┬─ text response → 返回用户
                                 └─ tool_calls → 并行/串行执行 → 结果注入上下文 → 循环
```

关键设计要点：

**并行工具执行**：当 LLM 返回多个 `tool_calls` 且所有工具均标记 `supports_parallel=True` 时，Agent 会使用 `ThreadPoolExecutor`（上限 8 线程）并发执行，大幅降低多工具调用的延迟。如果任一工具不支持并行，则退化为串行执行。

**可中断运行**：Agent 运行在独立线程中，通过 `threading.Event` 暴露取消机制。用户按 Ctrl+C 或通过 Dashboard 取消时，Agent 会在当前工具执行完毕后安全中断。每个工具都实现了 `cancel()` 方法——例如 `bash` 工具会终止子进程，`agent` 工具会递归取消子 Agent。

**中断恢复感知**：如果上一次运行被中断，Agent 会在新的上下文开头注入一条系统通知，告知 LLM 之前的工作被中断了，方便 LLM 判断是否需要继续或重新开始。

**迭代上限保护**：通过 `max_rounds` 配置项（默认 50）限制单次对话的最大 LLM 往返次数，防止无限循环消耗 token。

### 上下文管理：三层压缩

位于 `core/agent/context.py`，是 ATRI Agent 最核心的基础设施之一，实现了基于**触发进入/退出**的三层级联压缩机制（非滑动窗口）：

```text
第 1 层 (tool_snip)
  ├─ 触发阈值：~128K tokens
  ├─ 策略：将冗余工具输出替换为 <persisted-output> 标记
  ├─ 溢出：完整结果写入 data/tool_outputs/ 磁盘文件
  └─ 退出：上下文低于阈值后恢复内联

第 2 层 (summarize)
  ├─ 触发阈值：~192K tokens
  ├─ 策略：调用 LLM 自身对早期对话生成摘要
  ├─ 位置：摘要插入到最早保留消息之前
  └─ 退出：继续按需摘要更多旧消息

第 3 层 (hard_collapse)
  ├─ 触发阈值：~256K tokens
  ├─ 策略：丢弃除摘要和最近消息之外的所有内容
  └─ 保护：保留系统提示、最后一条用户消息、最后 N 条工具结果
```

Token 估算器支持 **CJK 感知**（中文字符约 1.5 token，拉丁字符约 0.25 token），比简单的 `len/4` 估算更精确。

### 工具系统

所有工具位于 `core/tools/`，继承自 `BaseTool`（定义于 `base.py`）。每个工具声明自己的 `name`、`description`、`parameters`（JSON Schema）以及 `ToolCapabilities`（是否支持并行、后台运行等）。

#### 文件与代码工具

| 工具 | 用途 |
| --- | --- |
| `bash` | Shell 命令执行，支持超时、确认提示、环境变量注入、子进程取消 |
| `edit` | 基于唯一字符串匹配的精确文本替换，仅发送 diff 而非全文 |
| `write` | 写入新文件或完全重写已有文件 |
| `read` | 读取文件（行号、偏移/限制、图片/PDF 支持） |
| `grep` | 正则内容搜索（ripgrep 风格），支持 glob 过滤、上下文行 |
| `glob` | 文件模式匹配，返回按修改时间排序的结果 |
| `list_dir` | 目录列表 |
| `tree` | 目录树可视化 |
| `search` | 组合文件系统搜索（grep + glob + list_dir + tree） |
| `find_replace` | 跨多文件查找替换 |
| `lint` | 代码 lint 与自动修复（ruff、mypy 等） |
| `terminal` | 伪终端管理，支持长时间运行的交互式进程 |

#### 音乐工程工具

| 工具 | 用途 |
| --- | --- |
| `midi` | `midi_query`/`midi_inspect` 查看工程；`midi_write` 写 notes；`midi_diff` 精确改事件；`midi_batch_edit` 批量改 velocity 与 CC curves |
| `music` | 本地音乐库搜索、播放、暂停、切歌、音量调节 |

#### Agent 编排工具

| 工具 | 用途 |
| --- | --- |
| `agent` | 子 Agent 完整生命周期管理，支持并行任务、后台任务、独立模型配置 |
| `mode` | Plan/Agent 模式运行时切换 |
| `task_result` | 轮询后台子 Agent 任务状态与结果 |
| `retrieve_tool_result` | 检索被压缩到磁盘的工具输出 |

#### 扩展与集成工具

| 工具 | 用途 |
| --- | --- |
| `mcp` | MCP 服务器管理、工具注册、代码生成（最大最复杂的工具，约 43KB） |
| `skill` | 本地 `SKILL.md` 技能文件管理与调用 |
| `web_search` | Tavily API 网络搜索 |

### 子 Agent 系统

`agent` 工具（`core/tools/agent_tool.py`，约 29KB）是 ATRI 最强大的工具之一。它允许主 Agent 像调用函数一样**派生子 Agent** 来并行处理复杂任务：

```text
主 Agent 收到 "重构 auth 模块并写测试"
  ├─ 子 Agent A (background): 重构 auth.py
  ├─ 子 Agent B (background): 重写 test_auth.py
  └─ 子 Agent C (foreground): 审查 PR 并汇总
```

每个子 Agent 拥有：

- **独立 LLM 实例**：可配置不同的模型和 provider（例如主 Agent 用 Opus，子 Agent 用 Haiku 降本）
- **独立工具集**：完整的工具实例副本，互不干扰
- **独立上下文**：不污染主 Agent 的上下文窗口
- **可配置行为**：通过 `task_config` 覆盖 system prompt、temperature、max_tokens 等

调度模式：

- **前台 (foreground)**：主 Agent 阻塞等待子 Agent 完成，直接获取结果
- **后台 (background)**：主 Agent 不等待，通过 `task_result` 工具稍后轮询
- **并行**：多个独立子 Agent 通过 `task_configs` 列表一次性派发，并发执行
- **任务 ID 追踪**：每个子 Agent 分配唯一 ID，存储到 `TaskStore`，支持跨会话查询

### Plan / Agent 双模式

位于 `core/agent/mode.py`，提供运行时模式切换：

| 模式 | 行为 |
| --- | --- |
| **Plan** | 只读 + 只设计。Agent 只能读文件、搜索代码、分析架构，禁止执行写入或命令 |
| **Agent** | 完全执行权。Agent 可以写文件、运行命令、修改工程 |
| **Chat** | 纯对话模式（预留），仅文本回复 |

系统提示会随模式切换动态变化。Plan 模式下，提示会强调 "只能分析和设计，不能动手"；Agent 模式下则给予完整执行权限。模式切换通过 `mode` 工具或在 Dashboard 中一键切换。

### Pipeline 处理管线

位于 `core/pipeline/`，采用**洋葱模型**（受 AstrBot 启发）组织消息处理流程：

```text
MessageEvent 进入
      │
      ▼
┌─ WakingCheckStage ──────────────────────────────┐
│  判断是否应响应此消息                               │
│  · WebChat: 始终唤醒                               │
│  · OneBot11 私聊: 始终唤醒                         │
│  · OneBot11 群聊: @提及 或 唤醒词 触发              │
└──────────────────────────────┬───────────────────┘
                               ▼
┌─ PreProcessStage ──────────────────────────────┐
│  消息规范化                                       │
│  · 去除前导 @提及                                  │
│  · 统一消息格式                                   │
└──────────────────────────────┬───────────────────┘
                               ▼
┌─ ProcessStage (核心，约 40KB) ───────────────────┐
│  创建/复用 Agent 实例                             │
│  · 管理 Session / Timeline / Task 存储            │
│  · 处理图片转录（若启用）                          │
│  · 调用 Agent.chat_async()                       │
│  · 通过 WebSocket 广播 tokens/tool_calls/thinking │
│  · 会话级 asyncio.Lock 防止并发冲突               │
└──────────────────────────────┬───────────────────┘
                               ▼
┌─ RespondStage ─────────────────────────────────┐
│  响应路由                                        │
│  · 回传结果到正确的平台 (OneBot11 / WebChat)       │
│  · 长消息自动分块 (4000 字符)                     │
│  · 转换为各平台的本地消息格式                      │
└─────────────────────────────────────────────────┘
```

### 平台适配器

位于 `core/platform/`，所有平台适配器继承自 `Platform` 基类，统一转换为 `MessageEvent` + `MessageChain`：

- **WebChat**：通过 Dashboard HTTP 接入，创建 `MessageEvent` + `asyncio.Future` 对，HTTP 端点阻塞等待 Pipeline 完成后返回
- **OneBot11**：通过 `aiocqhttp` 接入 QQ/QQ 频道，双向 WebSocket，支持私聊和群聊
- 统一消息模型支持 Plain、Image、At、Reply、Face、File 等多种消息段类型

### 会话与持久化

```text
data/
├─ sessions/          # 会话消息历史 (JSON)
│   └─ <session_id>.json
├─ tool_outputs/      # 压缩溢出的工具输出
├─ runtime/           # 运行时事件日志
├─ music_workstation/  # DAW 工程文件
│   └─ project.json
└─ music_cache/       # 音乐库扫描缓存
```

- **SessionStore**：保存/加载完整消息历史，支持多会话切换，文件名做 Windows 安全清理
- **RuntimeTimelineStore**：记录每次 LLM 调用、工具执行的运行时事件，用于调试和审计
- **TaskStore**：持久化后台子 Agent 任务的状态，支持跨会话查询和恢复

### LLM 抽象层

位于 `core/agent/llm.py`，提供统一的 LLM 调用接口：

- **多 Provider 支持**：OpenAI 兼容（DeepSeek、Qwen、Kimi、GLM、Ollama 等）+ Anthropic 原生 Messages API
- **流式响应**：通过 `on_token` 回调逐 token 推送，经 WebSocket 直达前端
- **Thinking Block**：支持 `on_thinking` 回调（Claude 的 extended thinking、DeepSeek-R1 的推理链）
- **Token 计数**：实时追踪 prompt/completion tokens，估算费用
- **重试与容错**：内置指数退避重试、速率限制处理

### MCP / Skills 扩展

- **MCP（Model Context Protocol）**：完整的 MCP 客户端实现（`core/tools/mcp.py`），支持 stdio/HTTP 传输，管理外部 MCP server 的工具注册与调用
- **Skills**：本地 `SKILL.md` 文件系统，按需加载领域知识和工作流模板，Dashboard 提供技能浏览/编辑/导入界面

### 数据流全景

```text
Dashboard Chat 输入 "帮我写一个 bassline"
       │
       ▼ HTTP POST /api/chat/send
  WebChatAdapter
       │ 创建 MessageEvent + Future
       ▼
  Pipeline Scheduler
       │ WakingCheck → PreProcess
       ▼
  ProcessStage
       │ 获取/创建 Agent 实例
       │ 加载会话历史
       │ 构建系统提示 (含当前工程 context)
       ▼
  Agent.chat_async()
       │ LLM 推理 → 决定调用 midi_write
       ▼
  midi_write 工具
       │ 写入 music_project.py
       │ 返回操作结果
       ▼
  Agent 继续 → 请求 Dashboard 同步
       │
       ▼ WebSocket broadcast
  Studio 前端实时显示新 notes
       │
       ▼ REST /api/studio/sync-to-host
  Rust Audio Host 同步并播放
```

## 首次配置建议

1. `Settings -> Providers` —— 添加 OpenAI 兼容或 Anthropic 兼容的模型 Provider。
2. `Settings -> Models` —— 激活要使用的模型。
3. `Settings -> Music` —— 添加本地音乐目录。
4. `Settings -> Audio` —— 确认采样率、缓冲区、位深与输出设备。
5. 打开 `Studio`，点击 Demo 或创建轨道，确认 Host Online 后播放。
6. 如需第三方乐器，先配置 VST 路径，再在 Studio Rack 中扫描并选择插件。

## DAW 工程模型

工程文件位于 `data/music_workstation/project.json`，包含：

- `title`、`tempo`、`time_signature`、`length_beats`
- `tracks`（音量、声像、静音、独奏）
- MIDI clips / audio clip placeholders
- 展开后的 notes / MIDI events
- 插件槽与 state chunks

`core/music_project.py` 负责 normalize、保存、迁移旧结构、展开 clip notes，确保 Agent 工具与 Dashboard 始终操作同一种工程格式。

## Agent 与音乐工程

Agent 不只是给文字建议。当前已配备 MIDI 专用工具：

- `midi_write` —— 向指定轨道写入一批 MIDI notes，支持 replace 或 append。
- `midi_diff` —— 对现有 notes 和 MIDI events 做 add/delete/update 级别的精确修改，并支持 velocity、CC、pitch bend、aftertouch 曲线。
- `midi_batch_edit` —— 用 selection + shape 批量编辑 velocity、CC、expression、modulation、pitch bend、aftertouch，例如 crescendo、swell、humanize、accent；写操作必须显式提供 `track_id`、`selection.track_ids` 或 `all_tracks=true`。
- `midi_query` —— 查看压缩摘要：轨道、clip、note 数量、velocity 统计、已有 CC/event lanes。
- `midi_inspect` —— 分页查看详细 notes/events，拿到精确 id、时间、力度、CC 值后再做局部编辑。
- `music_player` —— 搜索、播放、暂停、切歌、调音量。（这个是控制音乐播放器的）

MIDI 工具的公开时间参数默认使用工程时间线上的 absolute beat；只有显式传 `local_start` 时才表示 clip-local beat。这样 `midi_inspect` 返回的 `start` 可以直接交给 `midi_diff` 使用，非 0 起点 clip 会在内部自动换算。

典型对话示例：

```text
在 1 号轨道写一个 8 小节 C minor synthwave bassline，速度贴合当前工程。
```

Agent 会生成 beat-based MIDI、写入工程、请求 Dashboard 同步到 Rust Host。你可以在 Studio 里立刻看到 notes，也可以继续要求它做变奏、删掉某一拍、调整 velocity、绘制 CC1/CC11 曲线，或写入 pitch bend / aftertouch 表情。

## ASIO 构建 (Windows)

默认构建不启用 ASIO，WASAPI 等 CPAL 默认后端可直接使用：

```powershell
cd atri-host
cargo build -p atri-host
```

如需 Steinberg built-in ASIO 或其他 ASIO 设备：

```powershell
cargo build -p atri-host --features asio
```

前置准备：

1. 安装 **Visual Studio Build Tools**（C++ 桌面开发 + Windows SDK）。
2. 安装 **LLVM/Clang**，确保 `libclang` 可被找到。
3. 下载并解压 **Steinberg ASIO SDK**。
4. 设置环境变量：

```powershell
setx CPAL_ASIO_DIR "你的ASIOSDK放置路径"
setx LIBCLANG_PATH "C:\Program Files\LLVM\bin"
```

重开终端后构建。缺少依赖时 `asio-sys` 会在编译阶段报错。

### Audio Host 配置说明

`audio_host.audio_engine` 可选值：

- `default` —— 系统默认输出设备。
- `<host>` —— 指定 CPAL host 的默认设备，如 `wasapi`。
- `<host>::<device name>` —— 具体设备，如 `wasapi::Speakers (Realtek USB Audio)`。

`bit_depth` 支持 `f32`、`i16`、`i24`。

注意：CPAL 未直接暴露 24-bit sample format，`i24` 会优先匹配 `I32/U32` 等设备格式，实际可用性取决于声卡驱动。

## 开发命令

Python：

```powershell
uv run ruff check
uv run pytest
```

前端：

```powershell
cd frontend
npm install
npm run dev      # 开发服务器
npm run build    # 生产构建
```

Rust Host：

```powershell
cd atri-host
cargo fmt
cargo test -p atri-host
cargo build -p atri-host
```

## 项目结构

```text
ATRI/
├─ main.py                    # Python 入口
├─ config.yaml                # 运行配置
├─ core/
│  ├─ agent/                  # Agent 主循环、LLM、上下文、会话
│  ├─ tools/                  # 文件、终端、搜索、MIDI、音乐控制等工具
│  ├─ pipeline/               # 平台消息处理管线
│  ├─ platform/               # WebChat / OneBot11 适配
│  ├─ runtime/                # timeline、task store
│  ├─ plugin/                 # Python 插件系统
│  ├─ host.py                 # Rust Audio Host 进程管理
│  └─ music_project.py        # DAW 工程 JSON 模型
├─ dashboard/
│  ├─ server.py               # Quart Dashboard
│  ├─ music.py                # 音乐库、Studio、Host API
│  ├─ routes/                 # Auth、Chat、Settings、MCP、Skills 等路由
│  └─ static/                 # 前端构建产物
├─ frontend/
│  └─ src/
│     ├─ components/chat/     # Chat UI
│     ├─ components/music/    # Music、Player、Studio
│     ├─ components/settings/ # Provider、Models、Audio Host 设置
│     ├─ components/pages/    # Workspace、Adapters、MCP、Skills
│     └─ composables/         # API、DAW Host、Music、Auth、WS 状态
├─ atri-host/
│  ├─ atri-core/              # 音频 buffer、MIDI、tempo/time 类型
│  ├─ atri-engine/            # Session、Route、Transport、Synth、Processor
│  ├─ atri-host-bin/          # CPAL driver、IPC commands、editor host
│  └─ atri-vst3/              # VST scanner、factory、plugin wrapper
├─ tests/                     # Python 测试
├─ plugins/                   # 用户插件目录
├─ skills/                    # Skills 根目录
├─ workspace/                 # Agent 文件工作区
└─ data/                      # sessions、runtime、music cache、DAW project
```
## License

[GNU Affero General Public License v3.0](LICENSE)
