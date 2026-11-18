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
Vue UI
  ├─ Conversation workbench
  ├─ Video studio
  └─ Model settings
          │ REST + SSE
          ▼
FastAPI
  ├─ routes.py            HTTP contract, settings, productions
  ├─ service.py           conversation orchestration
  │     ├─ app_server.py  stdio JSON-RPC client
  │     ├─ protocol.py    input/event/approval mappings
  │     ├─ tools.py       dynamic tools (incl. start/get video production)
  │     └─ skills.py      SKILL.md lifecycle
  ├─ settings_store.py    public settings + write-only keys
  ├─ productions.py       upload / run / cancel / SSE / artifacts
  ├─ pipeline.py          ingest → decompose → intent → script → generate → compose
  ├─ providers.py         openai-compatible chat / video / TTS adapters
  ├─ references.py        real retrieval over skills/video-references
  ├─ media.py             ffmpeg / ffprobe helpers
  └─ db.py                SQLite persistence
                 ▼
         Codex CLI (conversations only)
         local ffmpeg (default video pipeline)
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

### `app_settings`

单行 JSON：多模态、视频生成、TTS 和合成开关。密钥与公开配置分开存；对外读取只出现 `api_key_set`，不会回显 Key。`PUT` 时省略或 `""` 保留原 Key，JSON `null` 清除。

### `video_uploads` / `productions` / `production_events`

参考视频元数据、创作任务快照（拆解、意图、骨架、脚本、clips、output、`replace_policy`、只读 `replace_plan`）以及按自增 ID 重放的管线 SSE。对话上传仍走 `POST /api/uploads`（20MB）；创作上传走 `POST /api/productions/uploads`（500MB），写入 `data/uploads/productions/`。产物在 `data/artifacts/productions/{id}/`，清理对话孤儿目录时会跳过 `productions/`。

## 7. 视频创作管线

管线独立于 Codex CLI，由 `PipelineEngine` 同步执行、后台线程启动。默认提供者 `local_ffmpeg`，无 API Key 也能跑完全程。

阶段固定为：`ingest` → `decompose` → `intent` → `script` → `generate` → `compose`。状态可落盘恢复；已有拆解结果时可再次 `POST /run`（默认 `resume_from=intent`，空 body 兼容），也可 `PATCH` 更新 `brief` / `replace_policy`。SSE 事件至少包括 `stage_started`、`stage_completed`、`warning`、`error`、`completed`。

- **decompose**：先用 `select='gt(scene,0.15)'` 切镜；切点不足则按 Hook/Value/CTA 时间窗切分。Hook/CTA 会吸附到合法窗口内最近的主切点（不限于目标前后 400ms）。无切点时 Hook/CTA 窗口为片头/片尾 `min(3000ms, max(1500ms, 15%))`。官方夹具切在 2700ms / 5400ms，分成 hook / value / cta 三段。片段覆盖源视频 `[0, duration_ms)`，允许 100ms 误差且互不重叠。每段写出 1–3 张关键帧，文件名为 `{seg_id}_frame_01.jpg`（以及 `_02` / `_03`），不把帧 URL 写进契约字段。若配置了远程多模态，本地切分后再做 refine，失败则保留本地结果并写 warning。
- **intent**：必须真实检索 `skills/video-references`：3 篇同品类 + 1 篇跨品类。此阶段只读首行骨架和第二条 `---` 之前的注意项；`intent.references[].path` 是相对项目根的真实路径。
- **script**：至少读 3 篇参考全文。镜头策略以解析后的 `replace_plan` 为准：`mode=default` 且 brief 未点名片段时，Hook/CTA `generate`、中间 Value `reuse`；也可按 `all` / `selected` / brief 点名任意段生成。结构化勾选优先于 brief。
- **generate**：reuse 按源片段裁切并保留原声；generate 默认对关键帧做 Ken Burns。无 TTS 时垫源片对应时间窗音频。默认硬烧字幕时不再叠一层 drawtext。H.264 yuv420p + AAC。远程失败且 `fallback_to_local=true` 时回退本地。分辨率跟随源视频（竖屏封顶 1080×1920，横屏长边 ≤1920）。
- **compose**：拼接镜头；generate/reuse 交界做短淡化（约 8–12 帧画面 + 80–120ms 音频）；可选把参考音频按 `bgm_volume` 垫为 BGM；由旁白/屏上字生成 `final.srt`（不含「时长 Nms」调试句，也不烧替换指令；有开场/中间/结尾时按角色拆句），默认烧录并可另存 sidecar；最后把音画锁到同一精确时长。TTS 未配置不阻塞出片。`final.mp4` 须能被 ffprobe 解析，时长约等于脚本总时长 ±100ms，视频与音频时长差很小，`pix_fmt=yuv420p` 且 `color_range` 不是 pc；生成与拼接强制 `setsar=1`。

CJK 字幕优先使用 `/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf`。

## 8. 前端

前端采用 Vue 3 单页应用：

- `ConversationSidebar`：搜索、最近/归档、会话状态。
- `MessageTimeline`：对话、推理、计划、命令、Tool、审批、澄清。
- `CapabilityPanel`：Skill 和 Tool。
- `SkillEditorModal`：创建/编辑 `SKILL.md`。
- `App.vue`：HTTP、SSE、模型、附件、会话操作和错误状态。

视觉方向是“深色剪辑台 + 纸张式对话稿”。贯穿消息的时间线使用由橙到蓝的裂变光束表示 Agent 执行轨迹；工具和命令保持低干扰、可折叠。

SSE 只负责触发增量刷新，SQLite 中的 `items` 是前端最终状态源；EventSource 自动重连时可以从头重放，最终状态自然收敛。

## 9. 与旧实现的关键差异

| 维度 | 原智能中枢 | Video Fission |
| --- | --- | --- |
| 数据库 | MongoDB | SQLite |
| 鉴权 | 用户/角色/管理员 | 本地单用户 |
| 会话范围 | 多 Profile + 业务上下文 | 自由对话单 Profile |
| 写代码 | Git Worktree | 固定只读沙箱 |
| Tool | 大量业务领域 Tool | 6 个通用/视频策划与创作 Tool |
| Skill | 用户目录、商店、物化 | 项目目录 + 用户目录 + UGC 共享参考库 |
| 前端 | 大后台中的一个页面 | 独立完整工作台 |
| 部署 | MongoDB/Redis 等基础设施 | Python + Node build + Codex CLI |

## 10. 验证结果

验证日期：2026-11-17。视频管线后端于同日补测。

- Codex CLI：`0.169.0`
- 后端测试（含管线、设置与指定片段替换）：`uv run pytest -q` → 35 passed, 1 skipped
- 夹具：`backend/tests/fixtures/sample-reference.mp4`
- 无 Key 本地跑通：可下载 ffprobe 可解析的 `final.mp4` 与 `final.srt`
- 真实 Codex 集成：1 passed
- 前端单元测试：4 passed
- 前端生产构建：passed
- Playwright E2E：3 passed

真实 E2E 已验证：

- 浏览器创建新对话
- Codex 返回指定标记
- `create_video_brief` 被真实调用
- Markdown 产物真实写入 `data/artifacts/`
- 用户 Skill 创建、停用、重新启用
- 页面刷新后会话、消息、Tool 轨迹和 Skill 均恢复
- Thread 分叉、归档和取消归档
- 390px 移动端下会话列表与能力抽屉可达
- 首次对话前上传的附件在创建会话后仍保留

E2E 使用独立 `.e2e-data/` 数据目录和独立后端端口，不接触正常运行数据。

