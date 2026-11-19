# Video Fission 视频生成管线任务书

> 2026-11-18 增量约束：视频生成已删除 `local_ffmpeg` Provider，只保留真实模型通道；
> `generate` 镜头必须由模型直接生成语音和音乐，无音轨即失败。当前阶段不生成 SRT、
> 不硬烧字幕，也不依赖 TTS 或额外 BGM。下方早期任务描述中与本节冲突的本地生成、
> 字幕和 TTS 路径均为历史记录。

状态：已锁定（Grok Bot，2026-11-17 21:35 Asia/Shanghai）
截止：2026-11-18 08:00 Asia/Shanghai 交付可运行真实项目
工作区：`/workspace/video-fission`（共享电脑）。不要改用户本机副本，不要 git 提交/推送，除非 Grok 另行批准。
审批：开发中的问题和权限全部由 Grok Bot 审批，不要夜间唤醒用户。

## 1. 目标

在现有 Codex 对话、Skill、Tool 能力之上，增加一条**第一类视频创作管线**：

1. 用户上传参考视频，并填写新视频创作说明。
2. 用项目中可配置的多模态模型，对整段参考视频做完整理解、切片（开头 Hook + 中间价值 + 结尾 CTA 等）、逐片段理解与起止时间。开发阶段默认走本地可运行实现，不依赖外部 Key。
3. 根据创作说明做意图理解与决策，使用现有 Skill（`creative-intent-script-skeleton`、`video-references`、`universal-ugc-video-script-generation`）检索创意库，并生成新片段创意脚本。
4. 用可配置的视频生成模型，按脚本创作新视频片段。开发阶段默认走本地可运行实现。
5. 用新片段和参考视频中需复用的片段，经 ffmpeg 合成完整视频并交给用户下载。
6. 加分项（本夜一并做，作为默认可关闭能力，不是阻塞项的借口）：口播/配音、背景音乐、字幕的合理融合。

现有对话工作台必须继续可用，不得拆掉或绕过现有 Skill 文件。

## 2. 非目标

- 不做完整 Agent Runtime，也不把管线绑死在 Codex CLI 上。管线是独立引擎；Codex 对话可调用同一引擎，但**没有 Codex / 没有模型 Key 时，默认本地提供者也必须能跑完全程**。
- 不删除、不改写参考库 630 篇脚本的内容。检索代码可以新增。
- 不引入 GPU 训练/本地大模型权重。
- 不把 API Key 写入 git、README 或前端日志。

## 3. 锁定的配置模型

设置持久化在 SQLite 表 `app_settings`（单行 JSON）。API Key 只写不回显，读取时用 `api_key_set: true/false`，值用 `null`。

多模态可在无 Key 时使用本地描述兜底；视频生成必须使用真实模型通道，默认 `seedance`。

```json
{
  "multimodal": {
    "provider": "local_ffmpeg",
    "base_url": "",
    "api_key_set": false,
    "model": "",
    "timeout_seconds": 120
  },
  "video_generator": {
    "provider": "seedance",
    "base_url": "https://api.giikin.com",
    "api_key_set": false,
    "model": "seedance-2-0-fast",
    "timeout_seconds": 600
  }
}
```

`provider` 枚举：

- 多模态：`local_ffmpeg` | `openai_compatible`
- 视频生成：`seedance` | `openai_compatible` | `generic_http`

`openai_compatible`：`{base_url}/chat/completions`（多模态/文本）或文档中写清的视频生成协议。请求带 `Authorization: Bearer <api_key>`，`model` 可配。

`generic_http`（视频生成）：`POST {base_url}` JSON `{model, prompt, duration_ms, width, height, fps}`，响应可为：

- 直接视频字节（`Content-Type: video/mp4`），或
- JSON `{ "url": "https://..." }` / `{ "job_id": "..." }`；若是 job，则 `GET {base_url}/jobs/{job_id}` 轮询直到 `status=completed` 且给出 `url`。

视频生成调用失败时阶段直接失败，不写本地关键帧、不静默回退。生成结果必须包含模型直接生成的语音和音乐/环境音；缺少音轨同样失败。

## 4. 锁定的 HTTP 契约

对话附件上传仍为 `POST /api/uploads`，上限保持 20MB。视频创作走下面的新接口，上传上限 **500MB**，接受 `video/mp4,video/quicktime,video/webm,video/x-m4v` 及常见后缀。

### 设置

- `GET /api/settings` → 上述 JSON（无明文 Key）
- `PUT /api/settings` → 部分更新；未传 `api_key` 或传空字符串表示保留原 Key；传 JSON `null` 表示清除 Key。
- `POST /api/settings/test` body `{ "target": "multimodal"|"video_generator" }` → `{ "ok": bool, "provider": "...", "detail": "..." }`。远端视频生成做一次最小探活。不要在 detail 里回显 Key。

### 创作任务（productions）

- `POST /api/productions/uploads` multipart 字段 `file` → `{ id, name, path, size, content_type, duration_ms, width, height, fps }`（ffprobe 后返回；探不到媒体信息则 400）。
- `POST /api/productions` JSON `{ "title"?: str, "brief": str, "video_upload_id": str }` → 创建 `draft`，返回完整对象。`brief` 必填，最长 20_000。
- `GET /api/productions` → `{ items: [摘要...] }`，新到旧。
- `GET /api/productions/{id}` → 完整对象。
- `POST /api/productions/{id}/run` → 进入 `running` 并异步执行管线；已在 running 则 409。完成后 `completed`，失败 `failed` 且保留 `error` 与已完成阶段产物。
- `POST /api/productions/{id}/cancel` → 取消。
- `DELETE /api/productions/{id}` → 删除记录与其 artifacts（不得删除 uploads 里仍被其它任务引用的源片，除非无引用）。
- `GET /api/productions/{id}/events?after_id=` SSE，事件形状与对话 events 类似：`{id, type, payload, created_at}`。类型至少包括 `stage_started`、`stage_completed`、`warning`、`error`、`completed`。
- `GET /api/productions/{id}/artifacts/{name}` → 受控下载（只允许该任务目录内文件名，禁止 `..`）。`final.mp4`、`final.srt`、预览图和片段 mp4 都走这里。前端播放用该 URL。

### Production 对象

```json
{
  "id": "prd_<hex>",
  "title": "",
  "brief": "",
  "status": "draft|running|completed|failed|cancelled",
  "current_stage": "ingest|decompose|intent|script|generate|compose|done",
  "error": null,
  "warnings": [],
  "source_video": {
    "upload_id": "",
    "filename": "",
    "path": "",
    "duration_ms": 0,
    "width": 0,
    "height": 0,
    "fps": 0
  },
  "decomposition": {
    "summary": "",
    "provider": "local_ffmpeg",
    "model": "",
    "segments": [
      {
        "id": "seg_01",
        "role": "hook|value|cta|other",
        "start_ms": 0,
        "end_ms": 2000,
        "summary": "",
        "visual": "",
        "audio": "",
        "on_screen_text": "",
        "reuse_recommended": false
      }
    ]
  },
  "intent": {
    "goal": "",
    "audience": "",
    "promise": "",
    "assumptions": [],
    "references": [{ "path": "skills/video-references/references/...", "why": "" }]
  },
  "skeleton": {
    "one_liner": "",
    "hook": "",
    "value": "",
    "cta": ""
  },
  "script": {
    "total_duration_ms": 0,
    "shots": [
      {
        "id": "shot_01",
        "role": "hook|value|cta|other",
        "duration_ms": 2000,
        "action": "reuse|generate",
        "source_segment_id": "seg_01",
        "visual": "",
        "voiceover": "",
        "on_screen_text": "",
        "prompt": ""
      }
    ],
    "audio_plan": {
      "voiceover_language": "zh",
      "bgm": "reuse_reference|none",
      "subtitles": true
    }
  },
  "clips": [
    { "shot_id": "shot_01", "path": "", "url": "", "provider": "local_ffmpeg", "duration_ms": 0 }
  ],
  "output": {
    "video_path": "",
    "video_url": "/api/productions/{id}/artifacts/final.mp4",
    "srt_path": "",
    "srt_url": "/api/productions/{id}/artifacts/final.srt"
  },
  "created_at": "",
  "updated_at": ""
}
```

时间字段一律毫秒整数。片段必须覆盖源视频 `[0, duration_ms)`，允许 100ms 误差，不得重叠。

健康检查 `GET /api/health` 增加 `ffmpeg` 和 `pipeline` 字段，不得破坏现有 `ok/service/runtime/database`。

## 5. 管线阶段（必须按序，可落盘恢复）

产物目录：`data/artifacts/productions/{id}/`。

### 5.1 ingest

复制或引用已上传源片，ffprobe 写入 `source_video`。

### 5.2 decompose

`local_ffmpeg`：

- 用 ffmpeg 场景检测（如 `select='gt(scene,0.30)'`）得到切点；切点过少时，按时间切成 Hook / Value / CTA 三段。
- 默认角色规则：开头 `min(3000ms, max(1500ms, 15%))` 为 `hook`，结尾同样窗口为 `cta`，中间为 `value`（过长可再切为多段 `value` 或 `other`）。场景切点落在这些窗口内时优先用场景切点，不要生硬切在说话中间（若检测不到音频，就按切点）。
- 每段抽 1–3 张关键帧到 artifacts，填写 visual/summary（基于时长、切点、画面尺寸的结构化描述也可以，但字段不能空）。
- 必须输出整片 `summary` 和每段 `start_ms/end_ms/role`。

`openai_compatible`：在本地切分与抽帧之后，把若干帧（建议 8–16 张、jpeg、控制总 payload）和时长、brief 发给多模态模型，要求严格 JSON 覆盖：整片理解、每段理解、角色、起止时间。模型返回的时间必须裁剪到合法范围并消解重叠；失败则保留本地切分并记 `warning`，不得让整个任务失败（除非用户关了 fallback 且没有本地结果——本地切分总应先完成）。

### 5.3 intent

实现一个 Skill 检索器，**真实读取** `skills/video-references`：先 `references/index.md` 或文件名粗筛，按 brief 关键词/品类选 **3 篇同品类 + 1 篇跨品类**；本阶段只读每篇首行骨架和第二个 `---` 之前的注意项。`intent.references[].path` 必须是真实存在的相对项目根路径。

然后按 `creative-intent-script-skeleton` 产出 `intent` + `skeleton`（一行骨架和 Hook/Value/CTA）。有可用 LLM（多模态同一 `base_url` 也可做纯文本 chat）时用模型；否则用本地规划器，但仍必须检索参考库，禁止伪造“已参考”。

### 5.4 script

按 `universal-ugc-video-script-generation`，对已选参考中至少 3 篇读全文（或在上下文超限时读完全分镜列表），产出镜头级 `script.shots`。

默认决策（用户 brief 明确要求时优先服从 brief）：

- Hook、CTA：默认 `generate`
- 中间展示商品/证据的价值段：默认 `reuse`，`source_segment_id` 指向对应原片段
- 若 brief 要求“全部新生成”，则全部 `generate`
- 若 brief 要求“尽量复用原片”，则只生成无法对齐的 Hook/CTA

每个 `generate` 镜头必须有可执行的 `prompt`（英文或中文均可，需包含画面、运动、时长、画幅）。`reuse` 镜头必须有 `source_segment_id`。

### 5.5 generate

对每个 `generate` 镜头生成与源片一致的分辨率、帧率、时长（允许 ±100ms）的 H.264 yuv420p + AAC MP4，不能是黑屏空文件。

`local_ffmpeg`：用该镜头关联的原片关键帧（或中间帧）做 Ken Burns/缓慢缩放平移，叠加 `on_screen_text`（无文字则用 `visual` 摘要），运动真实可见。

远端提供者按第 3 节调用。

`reuse` 镜头：ffmpeg 按 `start_ms/end_ms` 从源片精确切出（为拼接一致性可统一重编码）。

结果写入 `clips[]`。

### 5.6 compose（ffmpeg）

- 所有镜头统一到源片（或 1080x1920 若源片竖屏；横屏保持源片，最长边不超过 1920）分辨率与帧率。
- 按 script 顺序 concat。
- 音频加分项（默认开启，可在 settings.compose 关闭）：
  - `reuse` 段保留原声。
  - `generate` 段：若 TTS 已配置且有 `voiceover`，生成配音后混入；否则静音或铺垫低音量参考 BGM。
  - `mix_reference_bgm=true` 时，从参考视频抽取音轨作 BGM 床，在配音下按 `bgm_volume` 衰减混合。
- 字幕加分项（默认开启）：根据各镜头 `voiceover` 或 `on_screen_text` 生成 `final.srt`；`burn_in_subtitles=true` 时硬烧到画面，同时保留 sidecar SRT。
- 输出 `final.mp4`，ffprobe 可打开，时长约为 script 总时长（允许 0.5s 误差）。

## 6. 前端

在现有 Vue 3 工作台增加顶层模式切换，**不要拆掉对话页**：

- 对话（现有）
- 视频创作（新）
- 模型设置（新）

视觉延续现有风格，不要另起一套无关设计系统。要能在 1280 桌面和 390 窄屏使用设置与下载。

视频创作页：

- 上传/拖放参考视频 + 创作说明 +「开始创作」
- 阶段进度（与 SSE 同步，刷新后从 `GET /api/productions/{id}` 恢复）
- 拆解时间轴：角色、起止时间、理解文本、关键帧
- 意图、骨架、参考脚本路径、分镜表（reuse/generate）
- 片段预览、成片播放与下载 MP4/SRT
- 历史任务列表

模型设置页：三类提供者的 endpoint、API Key、模型、超时、fallback、合成开关；测试连接按钮。Key 输入框不要回填明文。

## 7. Codex 对话集成（不要阻塞管线）

在 `tools.py` 增加（或等价）工具：

- `start_video_production`：brief + 已上传文件路径/id，创建并启动 production
- `get_video_production`：查询状态与产物路径

工具只调用同一管线引擎。无视频任务时，对话行为与现在一致。

## 8. 测试与样例

- 用 ffmpeg 生成 `backend/tests/fixtures/sample-reference.mp4`：约 8 秒、有画面变化（例如三段不同底色 + 文字 Hook/Value/CTA）+ 简单音轨。纳入仓库。
- pytest 覆盖：设置 Key 掩码；本地 decompose 覆盖全时长且含 hook/value/cta；intent 引用的参考文件真实存在；run 全程在无外网、无 Key 下产出可 ffprobe 的 `final.mp4` 和 `final.srt`；取消与失败路径；现有测试保持通过。
- 前端至少一个 Playwright 用例：打开视频创作页、上传 fixture、填写 brief、跑完（可加长超时）并看到下载链接。若 E2E 环境受限，至少保证组件/页面在 `npm test` 或 e2e 中有稳定覆盖，并在 STATUS 写明。
- 现有对话 E2E 不得无故删除。

## 9. 文档

更新 `README.md` 与 `docs/ARCHITECTURE.md`：新管线、配置项、默认本地提供者、启动方式和验收步骤。不要把 Key 写进文档。

## 10. 分工

### Codex Bot（实现主力）

后端：settings、productions API、SQLite、管线引擎、三类提供者、Skill 检索、ffmpeg 切片/生成/合成/字幕音频、tools 注册、pytest、fixture 视频、README/架构中与后端相关的更新。先让 `POST /run` 在本地提供者下打通，再补远端适配器。

### Claude Bot（前端 + 交叉验证）

前端：模式切换、视频创作页、模型设置页、SSE、播放下载。完成后对后端契约做交叉验证：对照本任务书查漏，补测，修前端与契约不一致的地方。不要在未开 STATUS 变更的情况下改本任务书的字段名。

两边都在 `/workspace/video-fission` 工作。提交前拉齐，避免互相覆盖。契约变更必须先写到 `status/STATUS.md` 并等 Grok 确认。

## 11. 验收清单（08:00 前必须全部满足）

1. 在本机工作区：`uv sync --extra dev && npm install --prefix frontend && ./scripts/dev.sh` 可启动，打开 `http://127.0.0.1:5173`。
2. 默认无需任何 API Key，上传 fixture 或等价短视频 + 创作说明，跑完全程，下载到可播放的 `final.mp4`。
3. 界面可见：整片理解、Hook/Value/CTA（或更多）切片及起止时间、意图与骨架、真实参考脚本路径、新片段与复用片段、成片。
4. 模型设置可配置多模态与视频生成的端点、API Key、模型，并可测试。
5. 原有 Codex 对话（会话、上传、Skill、Tool）仍可用。
6. `uv run pytest -q` 通过。
7. 字幕 sidecar（及默认硬烧）和参考 BGM/原声融合已实现；TTS 未配置时不得阻塞出片。
8. README 有从零启动和「视频创作」说明。

## 12. 进度板

实时写 `status/STATUS.md`。阻塞立刻找 Grok，不要等用户。

---

## 增量需求：任意指定片段的生成与替换合成

更新：2026-11-17 22:52 Asia/Shanghai
性质：在已锁定契约上**只增不改**。不得改已有字段名；新增字段如下。默认 Hook/CTA 生成、中间复用的行为必须保留，作为 `replace_policy.mode=default` 且 brief 未点名片段时的结果。

### 目标

用户不但能重做 Hook（以及默认的 CTA），还能按需求指定**任意片段**生成新镜头，并用这些新镜头替换参考视频中的对应段，再合成完整成片。指定方式同时支持：创作说明里的自然语言、结构化策略、以及前端在拆解时间轴上勾选后重跑。

### 新增字段 `replace_policy`（请求）

可选。出现在：

- `POST /api/productions` 的 JSON（在原有 `title?` / `brief` / `video_upload_id` 上增加）
- `PATCH /api/productions/{id}`（新增接口；`status=running` 时 409）可更新 `title?`、`brief?`、`replace_policy?`
- `POST /api/productions/{id}/run` 的可选 JSON body：`{ "replace_policy"?: object, "resume_from"?: "ingest"|"decompose"|"intent" }`。空 body 必须保持兼容。
- Codex tool `start_video_production` 的可选参数 `replace_policy`

形状：

```json
{
  "mode": "default|all|selected|brief",
  "roles": ["hook", "value"],
  "segment_ids": ["seg_02", "seg_04"],
  "time_ranges": [{ "start_ms": 1500, "end_ms": 5500 }]
}
```

校验：`mode` 缺省为 `default`；`roles` 只允许 `hook|value|cta|other`；时间窗 `end_ms > start_ms`；未知字段忽略。缺省对象视为 `{ "mode": "default" }`。

### 新增字段 `replace_plan`（任务对象上，只读、解析后写入）

拆解完成后、写 script 之前解析并落库，之后每次按新策略重跑都要重写：

```json
{
  "policy": { "mode": "selected", "roles": [], "segment_ids": ["seg_03"], "time_ranges": [] },
  "resolved_segment_ids": ["seg_03"],
  "reasons": [{ "segment_id": "seg_03", "reason": "user_selected" }]
}
```

`reasons[].reason` 用短标签即可：`user_selected`、`role_selected`、`time_range`、`brief`、`default_hook_cta`、`generate_all`。

### 解析规则（必须可测）

在已有 `decomposition.segments` 上解析，覆盖 `[0, duration_ms)` 的每一段，得到 `action=generate|reuse`：

1. `mode=all`：全部 `generate`。
2. `mode=selected`：只根据 `segment_ids` ∪ `roles` ∪ `time_ranges` 命中的段 `generate`，其余 `reuse`。时间窗与某段重叠 ≥ 200ms，或重叠超过该段时长的 50%，即命中。若解析结果为空，`run` 返回 400，文案说明没有命中任何片段。
3. `mode=brief` 或 `mode=default`：先从 `brief` 解析显式目标（见下）。解析到了就按这些段 `generate`，其余 `reuse`。`全部新生成` / `all generate` → 全部生成。`尽量复用原片` / `reuse` 且未点名其它段 → 只生成 Hook/CTA。`mode=default` 且 brief 没有点名片段 → 保持原默认：Hook、CTA 生成，中间价值/其它段复用。

Brief 解析至少要覆盖这些说法（大小写/空格不敏感，中英皆可）：

- 角色：开场 / 开头 / Hook、中间 / 价值段 / 商品展示、结尾 / CTA / 行动号召
- 序号：第 N 段、第 N 个片段、seg_0N、镜头 N（1-based，对应拆解顺序）
- 组合：「把第二段和结尾重新生成」「只重做开场」「中间也要新生成」
- 时间：「1.5秒到5秒」「从 00:01.5 到 00:05.5」

结构化 `mode=selected` 的勾选优先于 brief 里的片段点名；brief 仍用于创意文案和 prompt。

### 重跑

任务已有拆解结果、且不在 `running` 时，允许再次 `POST /run`：

- 仅改替换策略或 brief：默认 `resume_from=intent`（跳过 ingest/decompose，重做 intent→script→generate→compose）。
- 需要重拆解时，调用方可传 `resume_from=decompose` 或 `ingest`。
- 新成片覆盖同目录 `final.mp4` / `final.srt`；旧 clips 中不再对应的文件可覆盖。`script.shots`、`clips`、`output`、`replace_plan` 更新为这一轮的结果。
- 仍是 running → 409。

### 前端

视频创作页在不拆对话工作台的前提下增加：

- 开始前：策略快捷选项（默认／全部新生成／按创作说明判断），可与 brief 一起提交。
- 拆解时间轴：每段可勾选「重新生成」；已完成后可点「按选中片段重新生成并合成」（`mode=selected` + `segment_ids`，`resume_from=intent`）。
- 明显标出每段实际是 generate 还是 reuse（以 `replace_plan` / `script.shots` 为准，不要只看 `reuse_recommended`）。

### 测试与多轮端到端验收（必须）

在现有无 Key 夹具上至少覆盖这些用例（pytest 为主，前端补 Playwright 勾选重跑）：

1. 默认：Hook+CTA 生成，价值段复用，成片时长与源片一致（允许 ±100ms）。
2. 只生成中间某段（如仅 `seg_03` / 仅 value），Hook 与 CTA 为从源片切出的 reuse。
3. 只生成 Hook，其余 reuse。
4. `mode=all` 全部生成。
5. 仅凭 brief：「把第二段和结尾重新生成」——不得靠测试里硬塞 segment_ids。
6. 仅凭 brief：「只重做开场」。
7. `time_ranges` 命中某段。
8. 完成后改勾选再跑：新 `final.mp4` 可下载，`replace_plan.resolved_segment_ids` 与勾选一致。
9. `mode=selected` 且未命中任何段 → 400。

成片共性断言：可播放、`pix_fmt=yuv420p`、`color_range` 不是 pc、有 `final.srt`、被替换段与 reuse 段边界时间正确。

验收时不要用修复前的旧产物 `prd_ed32*`、`prd_bef65*` 当证据；当前 8000 进程若是旧代码，先重启。

### 效果改进（本轮一并做）

先把已知 P2 收掉：Ken Burns / 拼接强制 `setsar=1`（生成段不要再出现 `2264:2265` 这种 SAR）。多轮 E2E 之后，根据实际成片列出可改进项（接缝、时长、文案贴合、音画）并改一轮能稳定提升的问题；改完再跑同一组用例回归。不要为「更好看」改契约字段。

未改事项：不 git commit/push，不把项目拷回用户本机，不打扰用户睡眠（除非用户先来问）。有问题找 Grok 批。
