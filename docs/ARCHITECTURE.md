# Video Fission Architecture

## 1. 原项目能力定位

原项目中的智能中枢并不是一个独立 Agent 服务，而是由四层组合而成：

1. `admin/src/views/CodexAgent.vue` 提供对话工作台、事件展示和交互操作。
2. `app/api/codex_agent.py` 提供会话、SSE、附件、审批、澄清和预览 API。
3. `app/services/codex_agent_service.py` 负责会话状态、Codex Thread 生命周期、事件归并、策略检查和业务集成。
4. `codex_app_server.py` / `codex_protocol.py` 直接驱动官方 `codex app-server --stdio`。

原服务还耦合了 MongoDB、Redis、用户鉴权、选品/反馈 Agent Profile、Git Worktree、预览服务、记忆和业务领域 Tool。Video Fission 只保留“自由对话中枢”主线。

## 2. 抽离边界

保留：

- Codex CLI 进程和 JSON-RPC
- Codex Thread/Turn
- 会话与消息历史
- SSE 实时事件
- 审批、澄清、停止和追问
- 模型选择、推理强度
- Skill 发现与生命周期
- Tool 注册、调用和审计
- 对话工作台

移除：

- 选品、采集、商品池、发布和业务流水线
- Agent Profile 的选品/反馈分支
- 用户、角色、SSO、分享和审计后台
- MongoDB、Redis 和外部调度器
- Git Worktree 代码修改模式
- 应用预览进程
- 企业级记忆注入和业务领域 Tool

## 3. 模块图

```text
Vue Conversation UI
  ├─ conversation list / transcript / approvals / clarifications
  └─ Skill and Tool capability deck
          │ REST + SSE
          ▼
FastAPI
  ├─ routes.py          HTTP contract and upload
  └─ service.py         conversation orchestration
          ├─ app_server.py   stdio JSON-RPC client
          ├─ protocol.py     input/event/approval mappings
          ├─ tools.py        safe dynamic tool registry
          ├─ skills.py       SKILL.md lifecycle
          └─ db.py           SQLite persistence
                 ▼
              Codex CLI
              app-server --stdio
```

## 4. Codex CLI 对接

### 进程模型

每个活跃会话按需创建一个 Codex CLI 子进程：

```text
codex app-server --stdio
```

`CodexAppServer` 通过 stdin/stdout 收发一行一个 JSON 对象。后台线程持续读取 stdout：

- 带 `id` 且有 `result/error`：完成对应的 RPC Future。
- 带 `method` 的通知：归并为会话事件并持久化。
- 带 `method + id` 的 Server Request：由独立线程处理审批、澄清或动态 Tool，再原路响应。

### 主要协议

| 方法 | 用途 |
| --- | --- |
| `initialize` / `initialized` | 建立客户端会话 |
| `skills/extraRoots/set` | 注册项目 Skill 根 |
| `skills/list` | 验证运行时实际发现的 Skill |
| `thread/start` | 创建 Codex Thread |
| `thread/resume` | 恢复持久化 Thread |
| `turn/start` | 开始一轮对话 |
| `turn/steer` | 在运行中追加输入 |
| `turn/interrupt` | 停止当前 Turn |
| `thread/fork` | 从上下文创建分叉 |
| `thread/archive` | 归档 Thread |
| `thread/compact/start` | 压缩上下文 |
| `thread/items/list` | 同步历史 |
| `model/list` | 获取可用模型 |
| `item/tool/call` | Codex 调用应用动态 Tool |
| `approval.*` / `item/fileChange/requestApproval` | 审批回包 |

### 安全策略

- Thread 和 Turn 固定为 `read-only`。
- 网络访问开启，但写文件、执行需要升级的操作必须经过用户审批。
- Tool 只访问显式传入的隔离工作区和应用产物目录。
- 动态 Tool 输入使用 JSON Schema 约束。
- 所有 Tool 调用写入 `tool_runs`，并同步进入事件流。

## 5. 会话状态机

```text
idle
  └─ send_message ─▶ running
       ├─ turn.completed ─▶ idle
       ├─ runtime error  ─▶ failed
       └─ interrupt      ─▶ idle

archived
  └─ unarchive ─▶ idle
```

`turn_generation` 区分同一会话的多次执行；`active_turn_id` 用于追问和中断。会话锁保证同一时间只有一轮主 Turn，避免并发写同一 Codex Thread。

## 6. 数据模型

SQLite 以 WAL 模式运行，单进程读写，适合独立本地部署。

### `conversations`

保存标题、运行状态、模型、推理强度、Sandbox、审批策略、`codex_thread_id`、当前 Turn、代次、归档时间和错误。

### `items`

统一保存消息和结构化执行项：

- `userMessage`
- `agentMessage`
- `reasoning`
- `plan`
- `commandExecution`
- `fileChange`
- `dynamicToolCall`
- `mcpToolCall`
- `webSearch`

`codex_item_id` 对同一会话内的流式增量做 upsert，防止重复消息。

### `events`

保存按自增 ID 排序的 SSE 事件。前端携带 `after_id` 重连，因此页面刷新或短时断网不会丢失事件。

### `approvals` / `clarifications`

持久化待处理交互和最终决定。运行时等待用户回包，超时后自动拒绝或结束。

### `tool_runs`

独立于对话消息保存 Tool 调用，便于审计和后续统计。

## 7. 前端

前端采用 Vue 3 单页应用：

- `ConversationSidebar`：搜索、最近/归档、会话状态。
- `MessageTimeline`：对话、推理、计划、命令、Tool、审批、澄清。
- `CapabilityPanel`：Skill 和 Tool。
- `SkillEditorModal`：创建/编辑 `SKILL.md`。
- `App.vue`：HTTP、SSE、模型、附件、会话操作和错误状态。

视觉方向是“深色剪辑台 + 纸张式对话稿”。贯穿消息的时间线使用由橙到蓝的裂变光束表示 Agent 执行轨迹；工具和命令保持低干扰、可折叠。

SSE 只负责触发增量刷新，SQLite 中的 `items` 是前端最终状态源；EventSource 自动重连时可以从头重放，最终状态自然收敛。

## 8. 与旧实现的关键差异

| 维度 | 原智能中枢 | Video Fission |
| --- | --- | --- |
| 数据库 | MongoDB | SQLite |
| 鉴权 | 用户/角色/管理员 | 本地单用户 |
| 会话范围 | 多 Profile + 业务上下文 | 自由对话单 Profile |
| 写代码 | Git Worktree | 固定只读沙箱 |
| Tool | 大量业务领域 Tool | 4 个通用/视频策划 Tool |
| Skill | 用户目录、商店、物化 | 项目目录 + 用户目录 |
| 前端 | 大后台中的一个页面 | 独立完整工作台 |
| 部署 | MongoDB/Redis 等基础设施 | Python + Node build + Codex CLI |

## 9. 验证结果

验证日期：2026-11-17。

- Codex CLI：`0.169.0`
- 后端基础测试：4 passed
- 真实 Codex 集成：1 passed
- 前端单元测试：4 passed
- 前端生产构建：passed
- Playwright E2E：2 passed

真实 E2E 已验证：

- 浏览器创建新对话
- Codex 返回指定标记
- `create_video_brief` 被真实调用
- Markdown 产物真实写入 `data/artifacts/`
- 用户 Skill 创建、停用、重新启用
- 页面刷新后会话、消息、Tool 轨迹和 Skill 均恢复
- Thread 分叉、归档和取消归档
- 390px 移动端下会话列表与能力抽屉可达

