# Video Fission

Video Fission 是一个从 `hot-product-system` 智能中枢能力中抽离出的独立项目。它只保留 Codex 对话、持久化、Skill 和 Tool，不依赖原项目的用户体系、选品流水线、MongoDB、Redis 或业务前端。

## 已实现

- Codex CLI `app-server --stdio` JSON-RPC 对接
- 每会话独立持久化 Codex Thread，支持断线后恢复
- 新建、重命名、归档、取消归档、删除、分叉、上下文压缩、历史同步
- SSE 实时事件流，持久化 Agent 回复、推理、计划、命令、文件变更和工具调用轨迹
- 持续追问（steer）、停止执行、审批和用户澄清
- 模型与推理强度选择
- 文件上传和附件上下文
- SQLite 数据存储，默认启用 WAL
- 项目内 Skill 目录、图形化创建/编辑/启停/删除
- 参考视频意图、UGC 脚本生成和 630 篇参考脚本共享技能链
- 受控动态 Tool 注册、执行与运行记录
- 响应式 Vue 3 对话工作台
- 后端单元测试、真实 Codex 集成测试和 Playwright 浏览器 E2E

## 系统要求

- Python 3.11+
- Node.js 20+
- 已安装并登录官方 Codex CLI
- 当前已验证版本：`codex-cli 0.169.0`

```bash
codex --version
codex login
```

## 启动

```bash
uv sync --extra dev
npm install --prefix frontend
./scripts/dev.sh
```

打开 `http://127.0.0.1:5173`。

也可以分别启动：

```bash
# 终端 1
cd backend
../.venv/bin/python -m uvicorn video_fission.main:app --host 127.0.0.1 --port 8000

# 终端 2
cd frontend
npm run dev
```

生产构建后，FastAPI 会直接托管 `frontend/dist`：

```bash
npm run build --prefix frontend
cd backend
../.venv/bin/python -m uvicorn video_fission.main:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000`。

## 配置

复制 `.env.example` 为 `.env`，按需设置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CODEX_BIN` | PATH 中的 `codex` | Codex CLI 可执行文件 |
| `VF_HOST` | `127.0.0.1` | 监听地址 |
| `VF_PORT` | `8000` | 后端端口 |
| `VF_WORKSPACE` | `data/workspace` | Codex 隔离工作区 |
| `VF_DATA_DIR` | `data` | 数据、附件、产物与 Skill 根目录 |
| `VF_CODEX_TIMEOUT_SECONDS` | `180` | 单次 RPC 超时 |
| `VF_APPROVAL_TIMEOUT_SECONDS` | `300` | 审批/澄清等待超时 |

环境变量由当前 shell 读取；项目不会自动覆盖已有 `.env`。

## 数据与目录

```text
data/
├── video_fission.db       # SQLite
├── workspace/             # Codex 只读工作区
├── uploads/               # 用户附件
├── artifacts/             # Tool 生成产物
├── skills/                # 用户 Skill
└── skills_disabled/       # 停用的用户 Skill
skills/                    # 随项目分发的内置 Skill
```

SQLite 保存以下数据：

- `conversations`：会话设置、Codex Thread ID 和运行状态
- `items`：对话消息与结构化执行项
- `events`：可重放的 SSE 事件
- `approvals`：审批请求和决定
- `clarifications`：Agent 提问和用户回答
- `tool_runs`：动态 Tool 的入参、输出与成功状态

## Tool

默认注册 4 个动态 Tool：

| Tool | 用途 |
| --- | --- |
| `workspace_snapshot` | 查看隔离工作区文件 |
| `read_workspace_file` | 读取受控工作区文本文件 |
| `conversation_metrics` | 查询当前会话的持久化统计 |
| `create_video_brief` | 生成并保存 Markdown 视频简报 |

Tool 定义位于 `backend/video_fission/tools.py`。新增 Tool 时注册名称、描述、JSON Schema 和执行函数；会话创建 Thread 时会自动把 Tool 契约下发给 Codex。

## Skill

内置 Skill 位于 `skills/`：

| Skill | 用途 |
| --- | --- |
| `video-brief` | 通用短视频策划简报 |
| `creative-intent-script-skeleton` | 理解创作说明和参考视频拆解，先收敛意图、概念和 Hook/Value/CTA 骨架 |
| `universal-ugc-video-script-generation` | 在骨架确认后生成镜头级分镜、片段复用/生成计划和音视频合成要求 |
| `video-references` | 10 个品类、630 篇 UGC 参考脚本；供上述两个创作 Skill 共享检索 |

参考脚本和全量索引导航位于 `skills/video-references/references/`。意图阶段读取首行骨架和注意项，完整脚本阶段读取至少 3 篇目标脚本全文。

用户可在右侧能力台创建 Skill，文件会写入 `data/skills/<slug>/SKILL.md`；停用时移动到 `data/skills_disabled/<slug>`。

新增对话或刷新 Runtime 时，应用通过 `skills/extraRoots/set` 注册：

```text
data/skills
skills
```

`GET /api/runtime/skills` 会启动一个临时 Codex Runtime 并返回真实发现的 Skill，而不是只读取本地文件。

## 测试

基础测试：

```bash
uv run pytest -q
npm test --prefix frontend
npm run build --prefix frontend
```

真实 Codex 集成测试：

```bash
RUN_LIVE_CODEX=1 uv run pytest backend/tests/test_live_codex.py -q
```

Playwright E2E：

```bash
cd frontend
npm run test:e2e
```

E2E 覆盖：

1. 页面连接 Codex Runtime
2. 创建新对话并收到真实模型回复
3. 调用 `create_video_brief` 并落盘
4. 创建、停用、重新启用用户 Skill
5. 刷新页面后恢复会话、工具轨迹和 Skill
6. 390px 移动端下会话列表和能力抽屉可达
7. 未创建对话时上传附件，发送后附件不会丢失

E2E 使用独立的后端进程和 `.e2e-data/` 数据目录，不会读写正常的 `data/` 对话。

详细的设计与抽离说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

