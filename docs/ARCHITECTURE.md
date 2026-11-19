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
- 本地账号、JWT 会话、管理员角色与企业 CAS 单点登录

移除：

- 选品、采集、商品池、发布和业务流水线
- Agent Profile 的选品/反馈分支
- 分享、运营和审计后台
- MongoDB、Redis 和外部调度器
- Git Worktree 代码修改模式
- 应用预览进程
- 企业级记忆注入和业务领域 Tool

## 3. 模块图

```text
Vue UI
  ├─ Conversation workbench
  ├─ Login / SSO gate
  ├─ Video studio
  └─ Model settings
          │ REST + SSE
          ▼
FastAPI
  ├─ auth.py              account/JWT/cookie dependencies
  ├─ sso.py               CAS ticket validation and local-user sync
  ├─ api/auth.py          login, SSO, logout and user administration
  ├─ routes.py            HTTP contract, settings, productions
  ├─ service.py           conversation orchestration
  │     ├─ app_server.py  stdio JSON-RPC client
  │     ├─ protocol.py    input/event/approval mappings
  │     ├─ tools.py       dynamic tools (incl. start/get video production)
  │     └─ skills.py      SKILL.md lifecycle
  ├─ settings_store.py    public settings + write-only keys
  ├─ productions.py       upload / run / cancel / SSE / artifacts
  ├─ pipeline.py          ingest → decompose → intent → script → generate → compose
  ├─ understand/          提示词、规范化、转场精修、切片、observe、GeminiGatewayClient
  ├─ providers.py         openai-compatible chat / real video model adapters
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

### `users` / `user_settings`

`users` 保存 bcrypt 密码哈希、角色、显示名以及 SSO `user_id` / `area_id` 映射。`token_version` 在密码或角色变化时递增，使已签发的旧 JWT 立即失效。`user_settings` 以用户 ID 为主键保存模型、端点和只写密钥，因此一个用户无法读取或覆盖另一个用户的 API Key。

### `conversations`

保存 `user_id`、标题、运行状态、模型、推理强度、Sandbox、审批策略、`codex_thread_id`、当前 Turn、代次、归档时间和错误。列表、详情、更新、删除、SSE 和运行操作都必须带当前用户条件。

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

单行 JSON：多模态与真实视频生成配置。密钥与公开配置分开存；对外读取只出现 `api_key_set`，不会回显 Key。`PUT` 时省略或 `""` 保留原 Key，JSON `null` 清除。

### `video_uploads` / `productions` / `production_events`

参考视频元数据、创作任务快照（拆解、意图、骨架、脚本、clips、output、`replace_policy`、只读 `replace_plan`）以及按自增 ID 重放的管线 SSE。两张业务表都带 `user_id`。对话上传仍走 `POST /api/uploads`（20MB）；创作上传走 `POST /api/productions/uploads`（500MB）。用户文件分别写入 `data/uploads/users/{user}/`、`data/artifacts/users/{user}/` 和 `data/workspace/users/{user}/`，目录名使用用户 ID 的 SHA-256 短摘要。

### 认证与授权

本地登录使用 bcrypt 哈希，成功后签发 HS256 JWT，并同时写入 `HttpOnly + SameSite=Lax` Cookie。API 接收 `Authorization: Bearer` 或同源 Cookie；SSE 依赖 Cookie，不把长期令牌暴露在 URL。SSO 按 CAS `ticket → loginId → user` 流程验证，随后按企业账号同步本地用户并签发相同格式的 JWT。未绑定的同名本地账号不会被 SSO 自动接管，已绑定身份则以稳定企业用户 ID 为主键继续同步。

所有 `/api` 资源路由均先生成当前用户，再在数据库查询和文件路径两层执行所有权约束。用户直接访问其他用户已知的资源 ID 时返回 404，避免通过 ID 探测资源是否存在。非只读请求同时校验浏览器 Origin，并通过 `no-store` 禁止缓存用户 API 响应。Skill 与 Tool 是全局能力，所有登录用户共享同一套配置；聊天侧能力台用于引用，配置统一放在系统设置。

## 7. 视频创作管线

管线独立于 Codex CLI，由 `PipelineEngine` 同步执行、后台线程启动。视频生成只允许真实模型通道，默认 `seedance`；`local_ffmpeg` 仅用于切段、拼接、淡化和音画对齐，不再作为视频生成 Provider。

阶段固定为：`ingest` → `decompose` → `intent` → `script` → `generate` → `compose`。状态可落盘恢复；已有拆解结果时可再次 `POST /run`。`resume_from` 允许 `ingest|decompose|intent|script|generate`（非法值报错）；省略时按跟进消息分类，默认仍兼容 `intent`。`script` 结束后进入 `awaiting_script_choice`，需 `POST /api/productions/{id}/select-script`（body 为 `{ "script_id" }`）后才进入 generate。也可 `PATCH` 更新 `brief` / `replace_policy`。SSE 事件至少包括 `stage_started`、`stage_completed`、`warning`、`error`、`completed`。

- **decompose**：由 `video_fission.understand` 增强，`pipeline._stage_decompose` 调用 `run_decompose`。设置开启 `transition_detection` 且安装了 `shot-detect` extra 时，先用 PySceneDetect + TransNetV2 做帧级转场检测，并把模型时间点吸附到真实切点；依赖缺失或失败则退化为 `select='gt(scene,0.15)'` 的 ffmpeg 场景切分，并写 warning。切点仍按 Hook/Value/CTA 时间窗规划（无模型时），Hook/CTA 会吸附到合法窗口内最近的主切点。官方夹具切在 2700ms / 5400ms，分成 hook / value / cta 三段。片段覆盖源视频 `[0, duration_ms)`，允许 100ms 误差且互不重叠。每段写出可播放的 `clips/{seg_id}.mp4`（H.264 yuv420p，有音轨则保留）和 1–3 张关键帧 `{seg_id}_frame_01.jpg`（以及 `_02` / `_03`）。`openai_compatible` / `gemini_gateway` 配置可用时，用移植后的提示词做整片理解、片段解析和 observe。Gemini 多模态请求使用至少 30720 的有效 Token 预算，整片拆分与片段解析为 120 秒，只有 observe 使用 `understand.observe_timeout_seconds`（默认 360）；整片拆分最多评估两个候选，HTTP 层由 `VF_GEMINI_MAX_CONCURRENCY` 限制单进程并发并在网络错误上做带抖动退避。`gemini_gateway` 的整片理解与片段分析会在 `artifacts/.../gemini_api_calls/{video_understanding,clip_analysis}/` 下按调用原子写入独立 JSON，保存完整响应、调用标签、输入形态、模型、耗时及失败错误；诊断记录写盘失败不影响管线。失败不得让 production 失败，保留本地切分并 warning。无 Key 或关闭 `observe` 时不插入空白观察卡。多模态设置按用户隔离。一级标签到 `role` 的映射固定为：开场钩子→hook，痛点具象/方案登场/卖点展示/效果展示/场景演示/信任构建→value，响应号召→cta，其他或空→other。
- **intent**：必须真实检索 `skills/video-references`：3 篇同品类 + 1 篇跨品类。此阶段只读首行骨架和第二条 `---` 之前的注意项；`intent.references[].path` 是相对项目根的真实路径。brief/消息中的落地页 URL 会经 SSRF 校验后抓取并抽取结构化卖点（`landing_page`）；无 URL 或抓取失败不得让管线失败，JS 重页面可提示用户粘贴正文。
- **script**：至少读 3 篇参考全文。每次产出恰好 3 个创意脚本候选（`script_a` / `script_b` / `script_c`），共享同一参考时间轴和开场连续块，仅角度与口播/画面文案不同；随后暂停等待用户选择，不自动进入 generate。镜头策略以解析后的 `replace_plan` 为准：`mode=default` 且 brief 未点名片段时，Hook/CTA `generate`、中间 Value `reuse`；也可按 `all` / `selected` / brief 点名任意段生成。结构化勾选优先于 brief。
- **generate**：进入本阶段前须已选定脚本；开始时会再应用 `replace_plan`。reuse 按源片段裁切并保留原片音轨；generate 必须调用真实远端模型，不再从关键帧做 Ken Burns。Seedance 按 OpenAPI 提交 `model/prompt/duration/aspect_ratio/resolution`，`options.generate_audio=true`；`input_contents` 只传参考视频，不传分离人声或 BGM。prompt 要求模型直接生成语音、音乐或环境音，画面内不得生成字幕或任何叠加文字。无音轨的生成片段直接失败，不回退 TTS、参考人声或静音轨。远程失败直接失败，不回退本地。分辨率跟随源视频（竖屏封顶 1080×1920，横屏长边 ≤1920）。
- **compose**：拼接镜头；generate/reuse 交界做短淡化（约 8–12 帧画面 + 80–120ms 音频）；不额外混 BGM。当前阶段不生成 `final.srt`、不硬烧字幕，也不提供字幕下载。最后把音画锁到同一精确时长。`final.mp4` 须能被 ffprobe 解析，时长约等于脚本总时长 ±100ms，视频与音频时长差很小，`pix_fmt=yuv420p` 且 `color_range` 不是 pc；生成与拼接强制 `setsar=1`。

## 8. 前端

前端采用 Vue 3 单页应用：

- `ConversationSidebar`：服务端搜索、最近/归档、Cursor 分页，以及运行中 / 已完成 / 工具警告 / 整体失败状态。
- `MessageTimeline`：对话、推理、计划、命令、Tool、审批、澄清，以及理解 / 片段 / 观察 / 成片卡片。
- `CapabilityPanel`：Skill 和 Tool 引用；创建与启停在系统设置中管理。
- `ModelSettings.vue`：每用户模型、密钥、`gemini_gateway` 可选 `token_url`，以及理解与切片开关。
- `VideoObserveCard.vue`：仅在至少一段 observe 非空时出现。
- `SkillEditorModal`：创建/编辑 `SKILL.md`。
- `App.vue`：HTTP、SSE、模型、附件、会话操作和错误状态。

视觉方向是“深色剪辑台 + 纸张式对话稿”。贯穿消息的时间线使用由橙到蓝的裂变光束表示 Agent 执行轨迹；工具和命令保持低干扰、可折叠。

SSE 只负责触发增量刷新，SQLite 中的 `items` 是前端最终状态源；EventSource 自动重连时可以从头重放，最终状态自然收敛。列表刷新与正文 Token 流已解耦：`reasoning.*`、`agent.message.*` 只刷新当前正文，只有 Turn 状态、Tool 完成、标题和归档变化才刷新列表；15 秒轻量轮询补齐后台会话完成态。

`GET /api/conversations` 返回轻量列表 DTO 和 `next_cursor`，不暴露工作目录、Thread ID 或指令正文，也不再逐会话统计未使用的 `item_count`。前端新建对话只创建本地草稿，首次发送时才落库。

## 9. 与旧实现的关键差异

| 维度 | 原智能中枢 | Video Fission |
| --- | --- | --- |
| 数据库 | MongoDB | SQLite |
| 鉴权 | 用户/角色/管理员 | 本地账号 + JWT + 企业 CAS SSO |
| 会话范围 | 多 Profile + 业务上下文 | 每用户独立自由对话 |
| 写代码 | Git Worktree | 固定只读沙箱 |
| Tool | 大量业务领域 Tool | 6 个通用/视频策划与创作 Tool |
| Skill | 用户目录、商店、物化 | 全局项目目录 + UGC 共享参考库 |
| 前端 | 大后台中的一个页面 | 独立完整工作台 |
| 部署 | MongoDB/Redis 等基础设施 | Python + Node build + Codex CLI |

## 10. 验证结果

验证日期：2026-11-18。

- Codex CLI：`0.169.0`
- 后端测试（含认证、用户隔离、管线与指定片段替换）：`uv run pytest -q` → 52 passed, 4 skipped
- 夹具：`backend/tests/fixtures/sample-reference.mp4`
- 真实视频 Provider 桩：可验证 ffprobe 可解析且带模型音轨的 `final.mp4`
- 真实 Codex 集成：1 passed
- 前端单元测试：18 passed
- 前端生产构建：passed
- Playwright E2E：6 passed

真实 E2E 已验证：

- 浏览器创建新对话
- Codex 返回指定标记
- `create_video_brief` 被真实调用
- Markdown 产物真实写入 `data/artifacts/`
- 系统设置中的全局 Skill 创建、停用、重新启用
- 页面刷新后会话、消息、Tool 轨迹和 Skill 均恢复
- Thread 分叉、归档和取消归档
- 390px 移动端下会话列表与能力抽屉可达
- 会话列表 Cursor 分页、服务端搜索、归档视图与聚合状态
- 空草稿延迟创建、工具警告提示与后台运行态轮询
- 首次对话前上传的附件在创建会话后仍保留
- 用户名密码登录、HttpOnly Cookie 会话、管理员成员管理、退出登录
- 两个用户之间无法读取彼此的会话、设置、上传视频、创作任务和产物
- CAS 身份同步到本地用户并签发与密码登录相同的 JWT

E2E 使用独立 `.e2e-data/` 数据目录和独立后端端口，不接触正常运行数据。

