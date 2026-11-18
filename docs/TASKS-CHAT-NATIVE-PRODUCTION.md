# 对话内视频创作：任务书

状态：已锁定（Grok Bot，2026-11-18 09:00 Asia/Shanghai）
工作区：`/workspace/video-fission`（共享电脑）。不要改用户本机副本，不要 git 提交/推送，除非 Grok 另行批准。
审批：开发中的问题和权限全部由 Grok Bot 审批。不要改既有管线引擎行为来迁就界面。

## 1. 目标

取消独立的「视频创作」界面和入口。用户只在「对话」里完成视频创作：

1. 在对话输入区上传操作/参考视频。
2. 用自然语言输入创作需求并发送。
3. 在**同一条对话的消息列表**中依次看到：视频理解、任务规划、原片与片段展示、成片播放与下载。
4. 在同一条对话里继续改需求、勾选片段并重跑，结果仍落在消息列表中。

「模型设置」保留。现有 Codex 对话工作台必须继续可用。视频管线仍是独立引擎：没有 Codex、没有模型 Key 时，默认 `local_ffmpeg` 也必须能在对话里跑完全程。

## 2. 非目标

- 不推翻 `docs/TASKS-VIDEO-PIPELINE.md` 已锁定的管线、阶段、replace_policy、产物契约。本任务只做**加法**和前端信息架构调整。
- 不把管线绑死在 Codex CLI 上。禁止「必须等 Agent 调工具才能出片」。
- 不删除、不改写参考库 630 篇脚本。
- 不引入新的视觉主题；沿用现有纸色/sage token，复用已打磨的播放器比例（16:9 铺满、9:16 居中、无大块黑边）。
- 不 git 提交/推送，不写回用户本机，除非 Grok 批准。
- 不要动占用 `127.0.0.1:5173` 的 URRE 前端。本项目前端若需重启，使用 `5174`（或文档所述的空闲端口），后端仍是 `8000`。
- 不要把 Playwright e2e 指回 5174；继续用隔离端口（现为 5175 + API 8001）。

## 3. 产品决策（已锁定）

### 3.1 入口

- `WORKSPACE_MODES` 去掉 `studio`。顶栏只保留「对话」和「模型设置」。
- 删除所有「视频创作」导航、独立页面入口、工作室左侧「历史任务」栏。
- `VideoStudio.vue` 不得作为页面继续挂载。可先抽共用组件，再删除页面本身；不得留下隐藏入口或死链。

### 3.2 主路径（无 Codex 也要能跑）

1. 用户在对话输入区选择视频。视频走创作上传，上限 **500MB**，类型与现网 `POST /api/productions/uploads` 一致。非视频附件仍走 `POST /api/uploads`，上限 20MB。
2. 用户输入创作说明并发送。后端在写入这条用户消息的同时，**自动创建并启动**一条绑定该对话的 production（`replace_policy.mode` 默认 `brief`）。不要等 Codex 调用 `start_video_production`。
3. 管线按既有阶段异步执行。每到关键阶段，向该对话 upsert 消息卡片（见 §4），并走对话已有的 SSE，让列表实时更新。
4. 成片卡片提供播放器、`final.mp4` / `final.srt` 下载、取消（仅 running）。
5. 拆解完成后，片段卡片可勾选片段，提供「重新生成所选片段」（`mode=selected` + `resume_from` 按既有契约）。也保留「按最新说明重跑」（`mode=brief`）。

只发视频、没有说明：把视频留在用户消息里，不启动管线，并在界面提示「补充创作说明后再发送」。

只发文字、没有新视频：

- 若该对话还没有 production，走现有 Codex 对话（普通聊天，不启管线）。
- 若该对话已有 production，且 Codex 可用，把这条说明交给 Agent（并告知当前 `production_id`，禁止再 start 一条重复任务；应查询或按说明重跑）。
- 若该对话已有 production，且 Codex 不可用，将这条文字视为新的 brief，对**最新一条**绑定 production 做 `mode=brief` 重跑（`resume_from` 默认 `intent`，与既有契约一致）。卡片更新或追加一轮成片，不要另开对话。

一条消息里同时有新视频和新说明：始终创建**新的** production，不覆盖旧任务。

### 3.3 消息列表里必须出现的卡片

按时间顺序出现在用户消息之后，使用稳定 item id，阶段更新时 **upsert**，禁止每条 SSE 追加一张重复卡：

| item_type | 稳定 id | 何时出现 | 内容 |
| --- | --- | --- | --- |
| `video_progress` | `{prd_id}:progress` | 任务一开始 | 状态、当前阶段、警告/错误、可取消 |
| `video_understanding` | `{prd_id}:understanding` | `decompose` 完成 | 整片理解摘要；分段角色、起止时间、要点；可播放的参考视频 |
| `video_plan` | `{prd_id}:plan` | `intent`/`script` 完成（有数据就更新） | 意图、骨架、replace_plan（哪些新生成/复用） |
| `video_segments` | `{prd_id}:segments` | `decompose` 完成即可出现，generate 后补预览 | 各片段预览、动作标记（新生成/复用）、勾选重跑 |
| `video_output` | `{prd_id}:output` | 有 `final.mp4` 或 compose 完成 | 成片播放（沿用现有横竖屏规则）、下载 MP4/SRT |

用户消息本身要能**内嵌播放**本次上传的参考视频，而不是只显示文件名图标。

### 3.4 历史与旧数据

- 对话列表里，绑定过 production 的会话显示一个小标记（例如「成片」或时长），不另做工作室历史栏。
- 打开对话必须能还原全部创作卡片（以持久化 items 为准）。
- 已有、没有 `conversation_id` 的 production（当前库里的历史任务）放到对话侧栏一个可折叠的「未归入对话的成片」。点击后：若当前有对话则绑定到当前对话并展开卡片；若没有对话则新建一个对话再绑定。不得丢弃这些历史成片。

### 3.5 创作策略控件

视频已挂到输入区时，输入区可提供一行紧凑选项：`默认 Hook+CTA` / `按说明判断` / `全部重做`。默认选「按说明判断」（`brief`）。「勾选片段」只出现在拆解后的片段卡片上，不进输入区。

## 4. 锁定的加法契约

既有 `/api/productions*` 行为保持兼容。空 `/run` body 仍可用。

### 4.1 Production 增加可选 `conversation_id`

- 表 `productions` 增加可空列 `conversation_id`（有值时外键到 `conversations(id)`，建议 `ON DELETE SET NULL`），并加索引。
- `POST /api/productions` 与工具 `start_video_production` 接受可选 `conversation_id`。工具若未传，则使用 tool context 里的对话 id。
- `GET /api/productions` 增加可选查询参数 `conversation_id`。
- `GET /api/productions/{id}` 与列表摘要均回传 `conversation_id`（没有则为 `null`）。
- 允许 `PATCH /api/productions/{id}` 把 `conversation_id` 从空绑定到一个已存在的对话（用于历史成片归入）；已绑定的值不能改到另一个对话（409 或 400，文档里写死一种）。

### 4.2 对话消息中的视频附件

用户消息 `payload.files[]` 允许一项 `kind: "production_video"`：

```json
{
  "kind": "production_video",
  "id": "upl_...",
  "name": "ref.mp4",
  "path": "",
  "size": 0,
  "content_type": "video/mp4",
  "duration_ms": 8100,
  "width": 1280,
  "height": 720
}
```

`_validated_files` 必须接受创作上传目录中的视频，而不能只允许 20MB 对话上传根目录。播放与下载 URL 用现有 `GET /api/productions/{id}/artifacts/...`；参考视频若尚未创建 production，用创作上传的可访问方式（若现网没有「按 upload id 播放」的接口，允许**补一条**只读 `GET /api/productions/uploads/{upload_id}` 或等价的受控媒体 URL，不得开放任意路径）。

### 4.3 自动启动

`POST /api/conversations/{id}/messages`：若本次 files 含 `kind=production_video` 且 text 非空，则在同一请求内创建并 `run` production（`conversation_id` 已设，brief 为该 text，replace_policy 取请求里可选的 `replace_policy`，缺省 `{"mode":"brief"}`）。响应仍是对话对象；可在响应中附带本次 `production_id`（加法字段，不破坏原形状）。

必须防重：同一条用户消息不得启动两次。若 Codex 随后调用 `start_video_production` 且 brief/upload 与对话中刚启动的任务重复，应返回已有任务，而不是再开一条。

### 4.4 对话 items upsert

管线阶段回调/事件处理里，按 §3.3 的稳定 id 写入或更新 `items`（`codex_item_id` 置空，避免被 Codex sync 覆盖或删除）。每张卡的 `payload` 至少包含 `production_id` 和渲染所需的阶段数据；不要让前端为了画一张卡再全量轮询才能工作。同时往该对话 `events` 追加例如 `item_upserted` / `production_updated`，让现有对话 SSE 刷新时间线。

禁止把卡片只做在前端内存里——刷新页面必须还在。

### 4.5 工具

- `start_video_production`：补 `conversation_id`；描述改为「对话里上传的 video_upload_id」，删除「video studio」措辞；按 §4.3 防重。
- 新增 `run_video_production`：对已有任务按既有 `POST /run` body（`resume_from`、`replace_policy`）重跑。供 Agent 在跟进说明时调用。
- `get_video_production` 保持。

## 5. 前端任务

- 输入区：视频选择、上传进度、预览、500MB 校验、策略行；非视频附件行为不变。
- `MessageTimeline`：渲染 §3.3 五种卡片和用户气泡内的视频播放。播放器复用已修复的横竖屏样式。理解/规划/片段的文案展示复用 `format.js` 的清洁规则（basename、不把 raw prompt 和「时长 Nms」直接铺在主文案里）。
- 从 `VideoStudio.vue` 抽出播放器、进度、片段列表、成片下载等共用组件，对话时间线引用它们；然后移除 studio 模式与页面。
- 侧栏：会话标记 + 「未归入对话的成片」。设置页仍走「模型设置」，不要把设置入口删掉。
- 保留并更新 `data-testid`：能在对话时间线完成「上传 → 发说明 → 出片 → 下载 → 勾选片段重跑」的 e2e。旧的 `mode-studio`、`video-studio-page`、`studio-*` 页面级用例要改写，不要依赖已删除的入口。聊天原有 e2e 必须继续通过。
- `WORKSPACE_MODES`、样式、顶栏、App 路由状态一并收口；不要留下 `workspaceMode === 'studio'` 的死代码。

## 6. 测试与验收

后端：

- `conversation_id` 的创建、查询、绑定、不可改绑。
- 带视频+说明的 `send_message` 自动启动；只视频不启动；防重。
- 阶段完成会 upsert 5 类卡片且 id 稳定。
- 无 Codex 时，已有 production 的纯文字跟进会 brief 重跑。
- 既有 productions / replace_policy / 空 `/run` body 回归仍通过。

前端：

- vitest 覆盖新组件/工具函数。
- Playwright：对话内完整管线（可用现有 `backend/tests/fixtures/sample-reference.mp4`），断言理解/规划/片段/成片卡片出现，成片可播，下载链接可用，勾选中间段重跑后该段为「新生成」，顶栏没有「视频创作」，「模型设置」仍在，原聊天用例通过。

Grok 验收（开发完成并交叉验证后）：

- 在 `http://127.0.0.1:5174/` 实操：16:9 与 9:16 各跑通一条对话内出片；首屏能看到成片、无大块黑边、可下载。
- 无「视频创作」入口；设置页可用。
- 普通无视频对话仍可用。
- 历史未绑定成片能从侧栏打开。
- 不回归既有管线技术验收（yuv420p、时长、SAR 等）。

## 7. 分工

并行开工，契约以本文为准。交叉验证对方的代码和测试，不是只看自己的部分。

**Codex Bot（后端 + 契约测试优先）**

- §4 全部后端：库字段、上传校验、自动启动、items upsert、SSE、工具防重与 `run_video_production`。
- 后端测试并跑 `uv run pytest`。
- 不改前端页面结构；若必须提供只读 upload 媒体 URL，把用法写进本文件附近的注释或 STATUS，并告诉 Claude。

**Claude Bot（前端 + e2e）**

- §5 全部前端：输入区、时间线卡片、抽组件、去掉 studio 入口、侧栏历史、样式。
- 改写/新增 Playwright 与 vitest。
- 后端未合入前，可先按本契约做卡片组件和假数据，但合并时必须接到真实 items/SSE，禁止长期用假数据冒充完成。

**交叉验证**

- Codex 合入后，Claude 只读复核契约与测试是否覆盖 §4，并接上真实 API。
- Claude 合入后，Codex 只读复核：入口已删、时间线卡片完整、e2e 覆盖主路径、没有破坏对话工作台。
- 两边都说通过后，Grok 做 §6 实操验收。未通过前不算完成。

## 8. 顺序建议

1. Codex 先落库字段、自动启动和 item upsert（后端可先被对话页消费）。
2. Claude 同时抽组件、做时间线卡片和去掉入口。
3. 对接 SSE / 真 API。
4. 补测试与交叉验证。
5. Grok 实操验收。

进度写入 `status/STATUS.md`。阻塞找 Grok，不要改本任务书已锁定的决策，除非 Grok 改口。
