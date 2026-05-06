# ATRI

AI Agent Framework — OneBot11 (QQ / Napcat) + WebUI Chat

ATRI 是一个可扩展的 AI 框架，支持通过 QQ 机器人（OneBot11 / Napcat）和 Web 控制台两种方式与 AI 交互。Agent 拥有文件读写、代码编辑、Shell 命令、代码搜索、子 Agent 等完整工具链，所有文件操作限定在工作区沙箱内。

## 功能

### 平台接入

- OneBot11 反向 WebSocket（Napcat QQ 机器人）
- 内置 WebUI Chat 控制台（Vue 3 SPA）
- 统一消息模型，适配器可扩展

### AI Agent

- 完整的工具链：文件读写、精确编辑、Grep/Glob 搜索、目录浏览、Bash/Terminal、子 Agent、Web 搜索
- 工具并行执行：LLM 返回多个工具调用时，通过线程池并发执行
- 子 Agent 支持并行：可同时派发多个子 Agent 任务（blocking 等待结果 / background 异步轮询）
- 流式输出（思考过程 + 工具调用实时推送给 WebUI）
- 三级上下文压缩（截断 → 摘要 → 硬折叠）
- 会话持久化（按 session 隔离，支持多会话切换）
- Ctrl+C 优雅取消：首次中断取消当前操作，再次中断安全退出

### 多模型

- OpenAI 兼容 API（支持 DeepSeek、OpenAI 等）
- Anthropic API 兼容（支持 Claude 系列模型）
- 多 Provider 管理，WebUI 中一键切换模型
- 支持 reasoning / thinking（DeepSeek-R1、Claude 等思考模型）

### 扩展系统

- Skills 系统（SKILL.md 注入专业指令，支持 zip 导入）
- 插件系统（自动发现 `plugins/` 目录下的插件）

### 安全

- 工作区沙箱：所有文件操作限定在工作目录内，防止越权访问
- 两级危险命令检测：拦截 `rm -rf`、格式化、`/dev/sda` 等高危操作
- Dashboard 认证：启动时自动生成 auth_token，保护 Web 控制台

### 音乐播放器

> 没错孩子们你们难道不觉得一个Agent里有个音乐播放器很神圣吗

- 本地音乐库扫描（支持 MP3/FLAC/WAV 等格式）
- Agent 可通过工具控制播放（play / pause / skip / volume）
- WebUI 内嵌播放器，支持歌词显示
- 播放模式切换：顺序 / 随机 / 单曲循环

## 快速开始

### 环境要求

- Python >= 3.11
- Node.js >= 18（仅构建前端时需要）

### 安装

```bash
# 克隆项目
git clone <repo-url> && cd ATRI

# 安装依赖
uv sync

# 配置 - 复制 config.yaml.example 为 config.yaml 并填入 API Key
cp config.yaml.example config.yaml

# 构建前端（可选，已预构建在 dashboard/static/）
cd frontend && npm install && npm run build && cd ..

# 启动
uv run python main.py
```

访问 `http://localhost:6185` 进入 Web 控制台。

### 配置

```yaml
# 当前模型
model: deepseek-chat

# API 连接
api_key: sk-your-api-key
base_url: https://api.deepseek.com/v1
api_format: openai  # openai 或 anthropic

# 激活的模型（可在 WebUI Settings 中管理）
active_models:
  - model: deepseek-chat
    provider: DeepSeek

# 模型提供商
providers:
  DeepSeek:
    base_url: https://api.deepseek.com/v1
    api_key: sk-your-api-key
    api_format: openai  # openai 或 anthropic
    models:
      - deepseek-chat
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

# 数据存储
sessions_dir: data/sessions
plugins_dir: plugins

# Dashboard（auth_token 缺失时自动生成）
dashboard:
  enabled: true
  host: 127.0.0.1
  port: 6185
  auth_token: ''

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

# 扩展（可在 WebUI 中管理）
mcp_servers: {}
skills: {}
```

## 项目结构

```text
ATRI/
├── main.py                      # 入口
├── config.yaml                  # 运行时配置
├── pyproject.toml               # 项目元数据与依赖

├── core/                        # 核心框架
│   ├── lifecycle.py             # 生命周期管理（启动/关闭编排）
│   ├── event_bus.py             # 事件总线
│   ├── agent/                   # AI Agent 子系统
│   │   ├── agent.py             #   主循环（LLM + 工具调用）
│   │   ├── llm.py               #   LLM 适配层（OpenAI / Anthropic 兼容，流式）
│   │   ├── context.py           #   三级上下文管理器
│   │   ├── prompt.py            #   System Prompt 构建
│   │   ├── session.py           #   会话持久化（JSON）
│   │   └── tools_bridge.py      #   工具发现与注册
│   ├── platform/                # 平台适配器
│   │   ├── base.py              #   抽象基类
│   │   ├── message.py           #   统一消息模型
│   │   ├── onebot11.py          #   OneBot11（QQ / Napcat）
│   │   └── webchat.py           #   WebChat（Dashboard HTTP）
│   ├── pipeline/                # 处理管线（洋葱模型）
│   │   ├── scheduler.py         #   管线调度器
│   │   ├── stage.py             #   阶段基类
│   │   └── stages/
│   │       ├── waking.py        #   #1 唤醒词检测
│   │       ├── preprocess.py    #   #2 消息预处理
│   │       ├── process.py       #   #3 核心处理（Agent 调用）
│   │       └── respond.py       #   #4 响应路由
│   ├── tools/                   # Agent 工具（全部工作区沙箱化）
│   │   ├── base.py              #   工具基类
│   │   ├── read.py              #   文件读取
│   │   ├── write.py             #   文件写入
│   │   ├── edit.py              #   精确字符串替换
│   │   ├── find_replace.py      #   批量查找替换
│   │   ├── grep.py              #   内容正则搜索
│   │   ├── glob_tool.py         #   文件名模式匹配
│   │   ├── search.py            #   组合搜索
│   │   ├── list_dir.py          #   目录列表
│   │   ├── tree.py              #   目录树
│   │   ├── bash.py              #   Shell 命令（危险命令拦截）
│   │   ├── terminal.py          #   持久 Shell 会话
│   │   ├── agent_tool.py        #   子 Agent 生成
│   │   ├── lint.py              #   Python 代码检查
│   │   ├── web_search.py        #   Web 搜索 / 网页抓取
│   │   └── music.py             #   音乐播放控制
│   ├── skills/                  # Skills 系统
│   │   └── skill_manager.py     #   SKILL.md 解析与管理
│   └── plugin/                  # 插件系统
│       ├── base.py              #   插件基类
│       └── manager.py           #   插件管理器

├── dashboard/                   # Web 控制台
│   ├── server.py                #   Quart 后端（REST API + WebSocket）
│   ├── music.py                 #   音乐 API 蓝图
│   └── static/                  #   前端构建产物

├── frontend/                    # Vue 3 前端源码
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.vue
│       ├── composables/         #   响应式状态管理
│       ├── components/
│       │   ├── chat/            #   聊天组件
│       │   ├── settings/        #   设置组件
│       │   ├── music/           #   音乐播放器
│       │   ├── pages/           #   功能页面
│       │   ├── activity/        #   活动栏
│       │   ├── layout/          #   布局
│       │   └── shared/          #   通用组件
│       └── styles/

├── plugins/                     # 用户插件目录
├── workspace/                   # Agent 工作沙箱
└── data/
    ├── sessions/                # 会话持久化数据
    └── music_cache.json         # 音乐库缓存
```

## API

|方法|路径|说明|
|---|---|---|
|GET|`/api/status`|系统状态|
|GET/POST|`/api/settings`|读取/更新生成参数|
|GET/POST|`/api/workspace`|读取/更新工作目录|
|GET/POST|`/api/adapter`|OneBot11 适配器配置|
|GET|`/api/provider/list`|模型提供商列表|
|POST|`/api/provider/save`|添加/更新提供商|
|POST|`/api/provider/delete`|删除提供商|
|POST|`/api/provider/models`|获取提供商模型列表|
|POST|`/api/provider/activate`|激活模型|
|POST|`/api/provider/deactivate`|停用模型|
|POST|`/api/provider/select`|切换当前模型|
|GET/POST|`/api/mcp/servers`|MCP Server 管理|
|DELETE|`/api/mcp/servers/<name>`|删除 MCP Server|
|GET/PUT|`/api/skills/<name>`|Skill 管理|
|GET|`/api/sessions`|会话列表|
|GET/DELETE|`/api/sessions/<id>`|会话详情/删除|
|POST|`/api/chat`|发送消息|
|GET|`/api/tools`|工具列表|
|POST|`/api/approve-command`|批准危险命令|
|WS|`/ws`|实时事件流（token / tool / thinking）|

## 前端开发

```bash
cd frontend

# 开发模式（HMR，代理后端 :6185）
npm run dev

# 生产构建（输出到 dashboard/static/）
npm run build
```

## Architecture

```text
Platform Adapters        Pipeline (Onion Model)        Agent
┌──────────────┐    ┌──────────────────────────┐    ┌───────────┐
│  OneBot11    │───▶│ Waking → Pre → Process   │───▶│  LLM      │
│  (QQ/Napcat) │    │          ↓               │    │  + Tools  │
├──────────────┤    │       Respond ◀──────────│    │  + Session│
│  WebChat     │───▶│                          │    └───────────┘
└──────────────┘    └──────────────────────────┘
        │                      │
        └──────────────────────┘
               Event Bus
```

消息经平台适配器进入事件总线，由管线调度器依次执行各阶段（洋葱模型），Process 阶段调用 Agent 执行 LLM + 工具循环，结果通过 Respond 阶段路由回对应平台。

## License

MIT
