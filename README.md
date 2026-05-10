# ATRI

AI Agent Framework — OneBot11 (QQ / Napcat) + WebUI Chat

ATRI 是一个可扩展的 AI Agent 框架，通过 QQ 机器人 (OneBot11 / Napcat) 和 Web 控制台两种渠道接入。
Agent 拥有完整的工具链——文件读写、代码编辑、Shell 命令、子 Agent、Web 搜索——所有操作限定在工作区沙箱内安全运行。

## 功能特性

### 平台接入

- **OneBot11 反向 WebSocket**：对接 Napcat / Lagrange 等 QQ 机器人，私聊和群聊均支持唤醒词检测
- **WebUI Chat 控制台**：Vue 3 SPA 内嵌仪表盘，实时流式输出，多会话管理，文件浏览，音乐播放器
- **统一消息模型**：平台适配器可扩展，新平台接入只需实现 Adapter 基类

### AI Agent

- **完整工具链**：19 个内置工具——文件读写、精确编辑、Grep/Glob 搜索、目录操作、Bash/Terminal、子 Agent、Web 搜索、代码检查等
- **并行工具执行**：LLM 返回多个工具调用时，线程池并发执行，大幅降低响应延迟
- **子 Agent 并行**：同时派发多个子 Agent 任务，支持 blocking（等待结果）和 background（异步轮询）两种模式
- **流式输出**：思考过程和工具调用实时推送到 WebUI / WS，中间结果即时可见
- **三级上下文压缩**：截断 → LLM 摘要 → 硬折叠，在长对话中保持上下文不爆炸
- **会话持久化**：按 session 隔离，JSON 存储，支持多会话切换与恢复
- **优雅取消**：Ctrl+C 首次中断当前操作，再次中断安全退出

### 多模型支持

- **OpenAI 兼容 API**：DeepSeek、Qwen、Kimi、GLM、Ollama 等
- **Anthropic API 兼容**：Claude 全系列（支持 thinking/reasoning）
- **多 Provider 管理**：WebUI 中一键添加、切换模型，无需重启
- **图片转录**：可选视觉模型，自动将图片转换为文本描述再送入聊天模型

### 扩展系统

- **Skills 系统**：SKILL.md 指令注入，支持 zip 导入，多目录自动发现
- **插件系统**：`plugins/` 目录自动发现，可接入管线阶段、注册工具、注册命令
- **MCP 支持**：Model Context Protocol Server 动态工具生成，WebUI 管理

### 安全

- **工作区沙箱**：所有文件操作限定在 `workspace/` 内，路径穿越检测
- **两级危险命令拦截**：正则匹配 + 黑名单，拦截 `rm -rf`、格式化、裸设备写等高危操作
- **Dashboard 认证**：PBKDF2 密码哈希，Cookie 会话，登录限流

### 音乐播放器

- 本地音乐库扫描（MP3 / FLAC / WAV 等）
- Agent 可通过工具控制播放（play / pause / skip / volume）
- WebUI 内嵌播放器，歌词显示，播放模式切换

## 快速开始

### 环境要求

- Python >= 3.11
- Node.js >= 18（仅构建前端时需要）

### 安装与启动

```bash
# 克隆
git clone <repo-url> && cd ATRI

# 安装依赖
uv sync

# 配置
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入 API Key 等必要配置

# 构建前端（可选，已预构建在 dashboard/static/）
cd frontend && npm install && npm run build && cd ..

# 启动
uv run python main.py
```

访问 `http://localhost:6185` 进入 Web 控制台。首次启动时设置用户名和密码。

### 配置参考

```yaml
# 当前使用的模型
model: deepseek-chat

# API 连接
api_key: sk-your-api-key
base_url: https://api.deepseek.com/v1
api_format: openai          # openai | anthropic

# 模型提供商（可在 WebUI 中管理）
active_models:
  - model: deepseek-chat
    provider: DeepSeek

providers:
  DeepSeek:
    base_url: https://api.deepseek.com/v1
    api_key: sk-your-api-key
    api_format: openai
    models:
      - deepseek-chat
      - deepseek-reasoner
  Anthropic:
    base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-api-key
    api_format: anthropic
    models:
      - claude-sonnet-4-6

# 生成参数
max_tokens: 20000
temperature: 0.5
max_context_tokens: 1000000
max_rounds: 50

# Agent 行为
wake_words:
  - atri
persona: ''
extra_instructions: ''

# 工作目录（Agent 文件操作沙箱）
workspace: ./workspace
sessions_dir: data/sessions
plugins_dir: plugins

# Dashboard
dashboard:
  enabled: true
  host: 127.0.0.1
  port: 6185
  username: admin
  password: ''             # 为空时首次访问进入注册页

# OneBot11（QQ 机器人）
onebot11:
  enabled: true
  ws_reverse_host: 0.0.0.0
  ws_reverse_port: 6199
  ws_reverse_token: ''

# 音乐播放器
music_directories:
  - /path/to/your/music

# Web 搜索（Tavily API，可选）
tavily_api_key: ''
```

## 架构

```text
Platform Adapters        Pipeline (Onion Model)         Agent Core
+----------------+    +----------------------------+    +-----------------+
|   OneBot11     |--->| Waking -> Pre -> Process   |--->|  LLM            |
|   (QQ/Napcat)  |    |                    |       |    |  + Tools (x19)  |
+----------------+    |                    v       |    |  + Session      |
|   WebChat      |--->|                 Respond ---|    |  + Context      |
+----------------+    +----------------------------+    +-----------------+
        |                          |
        +------------+-------------+
                     |
                 Event Bus
```

消息经平台适配器进入事件总线，由管线调度器按洋葱模型依次执行四个阶段：

1. **Waking** — 唤醒词检测，决定是否响应
2. **PreProcess** — 消息预处理（去除 @前缀 等）
3. **Process** — 核心处理：创建 Agent，执行 LLM + 工具循环
4. **Respond** — 响应路由回来源平台

## 项目结构

```text
ATRI/
├── main.py                     # 入口
├── config.yaml                 # 运行时配置
├── pyproject.toml              # 项目元数据与依赖
├── Makefile                    # 开发命令（lint/test/format/ci）

├── core/                       # 核心框架
│   ├── lifecycle.py            #   生命周期管理
│   ├── event_bus.py            #   事件总线
│   ├── agent/                  #   AI Agent 子系统
│   │   ├── agent.py            #     主循环（LLM + Tool）
│   │   ├── llm.py              #     LLM 适配层（OpenAI/Anthropic，流式）
│   │   ├── context.py          #     三级上下文压缩
│   │   ├── prompt.py           #     System Prompt 构建
│   │   ├── session.py          #     会话持久化
│   │   └── tools_bridge.py     #     工具发现与注册
│   ├── platform/               #   平台适配器
│   │   ├── base.py             #     抽象基类
│   │   ├── message.py          #     统一消息模型
│   │   ├── onebot11.py         #     OneBot11
│   │   └── webchat.py          #     WebChat
│   ├── pipeline/               #   处理管线
│   │   ├── scheduler.py        #     调度器
│   │   ├── stage.py            #     阶段基类
│   │   └── stages/             #     Waking / PreProcess / Process / Respond
│   ├── tools/                  #   Agent 工具（全部沙箱化）
│   ├── skills/                 #   Skills 系统
│   └── plugin/                 #   插件系统

├── dashboard/                  # Web 控制台
│   ├── server.py               #   Quart 后端（REST + WebSocket）
│   ├── music.py                #   音乐 API
│   └── static/                 #   前端构建产物

├── frontend/                   # Vue 3 前端源码
│   └── src/
│       ├── composables/        #   响应式状态
│       └── components/         #   Chat / Settings / Music / Workspace ...

├── tests/                      # 测试套件
├── plugins/                    # 用户插件目录
├── workspace/                  # Agent 工作沙箱
└── data/                       # 运行时数据（sessions / music_cache）
```

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/api/status` | 系统状态 |
| GET/POST | `/api/settings` | 生成参数 |
| GET/POST | `/api/workspace` | 工作目录配置 |
| GET/POST | `/api/adapter` | OneBot11 配置 |
| GET/POST/DELETE | `/api/provider/*` | 模型提供商 CRUD |
| POST | `/api/provider/select` | 切换当前模型 |
| GET/POST/DELETE | `/api/mcp/servers/*` | MCP Server 管理 |
| GET/PUT | `/api/skills/<name>` | Skill 管理 |
| POST | `/api/skills/import` | Skill zip 导入 |
| GET/DELETE | `/api/sessions/*` | 会话管理 |
| POST | `/api/chat` | 发送消息 |
| GET | `/api/tools` | 工具列表 |
| POST | `/api/approve-command` | 批准危险命令 |
| WS | `/ws` | 实时事件流（token / tool / thinking） |

## 前端开发

```bash
cd frontend

# 开发模式（HMR，代理到 :6185）
npm run dev

# 生产构建（输出到 dashboard/static/）
npm run build
```

## 开发命令

```bash
make lint        # Ruff + ESLint
make format      # Ruff format
make typecheck   # mypy
make test        # pytest
make ci          # lint + typecheck + test（与 CI 一致）
```

## License

MIT
