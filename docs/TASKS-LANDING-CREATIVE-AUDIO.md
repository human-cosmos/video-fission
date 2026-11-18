# 落地页卖点、三脚本候选、参考音频与字幕硬烧：任务书

状态：已锁定（Grok Bot，2026-11-18 16:30 Asia/Shanghai）
工作区：`/workspace/video-fission`（共享电脑），当前 HEAD 为 `7713b2a`（`refactor: simplify workspace settings and navigation`，其上已有 Seedance `0d0865f` 与开头连续块 `b5cb8a5`）。
审批：开发中的问题和权限全部由 Grok Bot 审批。契约变更先写到 `status/STATUS.md` 并等 Grok 确认。
约束：不要改用户本机副本，不要 git 提交/推送，除非 Grok 另行批准。不要动占用 `127.0.0.1:5173` 的 URRE 前端。本项目前端若需重启，使用 `5174`；后端仍是 `8000`。Playwright e2e 继续用隔离端口（5175 + API 8001）。不要配置 Seedance / 多模态 / TTS Key，也不要走真实视频生成出片；本轮以功能实现和可验证的请求体、状态机、分离结果、字幕样式为准。

## 1. 目标

在现有「理解 → 意图 → 脚本 → 生成 → 合成」对话创作流水线上做加法，让用户可以：

1. 在对话里给出落地页地址，系统抓取并分析卖点，把卖点写进视频创作提示词。
2. 每次生成 **3 个**创意脚本，由用户挑选后再生成视频；节奏必须与参考视频一致（沿用现有镜头时长与开头连续块）。用户改说明后可再次生成。
3. 识别并分离参考视频中的口播（人声）与背景音乐，生成时作为参考音频传给视频生成模型。
4. 生成时禁止视频模型烧字幕；成片阶段再硬烧字幕，样式和位置尽量与参考视频一致。
5. 生成的视频必须有口播。

用户已确认：本轮不必真实调用 Seedance 出片，把功能跑通、用单测和本地/无 Key 路径验证即可。

## 2. 非目标

- 不推翻已锁定的 `docs/TASKS-VIDEO-PIPELINE.md`、`docs/TASKS-CHAT-NATIVE-PRODUCTION.md`、`docs/TASKS-SLOT-UNDERSTANDING.md`、`docs/TASKS-OPENING-SPAN.md`。开头连续块、卡片稳定 id、无 Key 时管线必须可跑、认证与 Gemini 接入、默认 `local_ffmpeg` 全部保持。
- 不把 hook 与开头重新混为一谈，不改开头吸附规则。
- 不恢复 `7713b2a` 已删除的能力侧栏、顶栏溢出菜单或旧的 `CapabilityPanel`。设置和账号入口保持在侧栏。
- 不配置、不调用真实 Seedance / 视觉模型 / TTS Key，不做正式出片验收。
- 不在本轮做落地页的无头浏览器渲染（JS 重度页面允许提示失败并让用户粘贴正文）。
- 不删除硬 Agent 约束，不改参考库现有脚本，不接入 OSS / GCloud / 素材库，不动 5173。
- 不把官方 e2e 指到 5174。
- reuse 镜头若源片已经硬烧了原字幕，本轮不要求抹除原字幕。新生成镜头必须禁止模型烧字；合成阶段按新脚本口播统一硬烧。

## 3. 现状与为何要改

- 对话发送参考视频和说明后会自动开任务，脚本阶段只生成一份，随即进入 generate / compose，没有「先选脚本再出片」的停顿。
- `resume_from` 目前只允许 `ingest|decompose|intent`。跟进说明默认从 `intent` 重跑，无法区分「重出 3 个脚本」和「已选脚本后只再生成画面」。
- 工作区没有落地页抓取或卖点抽取。意图和脚本只用 brief、拆解和参考库。
- Seedance 文档与接口已支持 `input_contents`（`video_url` / `audio_url`，角色含 `reference_video`、`reference_audio`）以及 `options.generate_audio`。当前 `generate_seedance_video` 只传 prompt / duration / aspect_ratio / options，没有参考音视频。
- 合成默认 `mix_reference_bgm=true`，把参考片**整段混音**抽成 `reference-audio.m4a`，按 0.18 音量叠进成片。没有人声/伴奏分离。`providers.synthesize_speech` 已存在但未接入。
- `_stage_generate` 若模型结果没有音轨，会回退叠源片时间轴音频或补静音。这与「必须有口播、且不要把原混音再叠一遍」冲突。
- `burn_subtitles` / `subtitle_burn_layout` 使用固定底部白字（Droid Sans Fallback，Alignment=2，MarginV 约 20% 高度），为了避开播放器控件，并不读取参考片字幕样式或位置。

## 4. 产品决策（已锁定）

### 4.1 落地页卖点

- 从用户消息和 brief 里识别 `http://` / `https://` 落地页链接；也允许后续用明确字段传入。没有链接时，管线照常只根据 brief 和参考视频工作，不得失败。
- 抓取 HTML（超时、体积上限、只允许 http/https）。必须做 SSRF 防护：拒绝回环、链路本地、私网和云元数据地址，禁止 `file://`。
- 抽出可读正文后，结构化为卖点信息，至少包括：`product`、`selling_points[]`、`benefits[]`、`audience`、`promises[]`、`source_url`、`raw_excerpt`、`method`、`parse_status`（`ok|partial|failed`）。
- 有对话/意图模型时，用模型从正文提炼卖点；无 Key 时用标题、meta、小标题和列表做本地抽取，并标明 `method=local_extract`。
- JS 重度或抓取失败：写入 warning，允许用户在对话里粘贴页面正文后再解析；不得把失败伪装成已完整分析。
- 卖点结果写入 production（建议字段 `landing_page`），并进入意图、脚本和视频生成提示词。规划卡可摘要展示卖点。

### 4.2 三次创意脚本与挑选

- 脚本阶段每次生成 **恰好 3 个**候选，共享同一条参考时间轴：各镜 `start_ms` / `end_ms` / `duration_ms`、角色、`action`（reuse/generate）、`source_segment_id`，以及开头连续块都必须一致。只允许改切入角度、口播、画面提示和 on-screen 文案。
- 3 个候选要能看出不同卖点角度，不能是同一份文案复制三遍。无 Key 时也必须出 3 份本地变体。
- 脚本阶段结束后，任务状态改为 `awaiting_script_choice`，**停止**，不得自动进入 generate。进度卡要写清「请选择创意脚本」。
- 用户挑选后，把选中项写入 `script` 和 `selected_script_id`，再继续 generate → compose。
- 用户改说明后再次生成：
  - 若任务还在等选择，或跟进明显是改文案 / 卖点 / 换个脚本，则重新生成 3 个脚本并再次停下。
  - 若已经选定脚本，且跟进是改画面或「再生成视频」，则从 generate 重跑，不丢弃已选脚本，除非用户同时要求换脚本。
  - 「用第 2 个」「选这个」视为挑选，不是重出脚本。
- `resume_from` 加法扩展为 `ingest|decompose|intent|script|generate`。旧调用不传时行为除「脚本后暂停」外保持兼容。
- 既有卡片稳定 id 不改。在 `video_plan` 的 payload 上加法展示 3 个候选和选中态，不要新增一种卡片类型。

### 4.3 人声与 BGM 分离，作为参考音频

- 在理解/拆解之后（生成之前）对参考视频做一次分离，产物至少为 `reference-vocals.m4a` 与 `reference-bgm.m4a`，记录 `method` 与 warning。
- 优先可用的音源分离（例如已安装的 Demucs）；未安装时必须有本地两路分离，产出两份**不同**的音轨，不得把原混音复制成两份充数。可用中置/侧边（mid/side）人声提取或同等本地方法，`method` 如实记录（如 `demucs` / `local_ms`）。
- 生成模型请求应带上：该镜的参考视频，以及人声、BGM 两路参考音频（Seedance 的 `input_contents`：`type=video_url` `role=reference_video`，两个 `type=audio_url` `role=reference_audio`）。`options.generate_audio` 必须为 `true`。
- 提示词必须要求：画面中要有与 `shot.voiceover` 一致的口播；禁止出现字幕、标题、角标或任何烧进画面的文字。
- 合成时只把分离后的 BGM 按设定音量混入，禁止再叠整段原混音，避免口播重复。
- 模型（或本地生成）结果若没有音轨，必须回退补上口播：优先接已有 `synthesize_speech`，或按该镜时间对齐参考人声。禁止用静音片充数。reuse 镜头可保留源片对应时间的声画，但仍要遵守合成阶段的 BGM 策略，不得把整段原混音再叠一层。

### 4.4 字幕只在合成时硬烧，并跟参考样式

- 生成阶段不得让模型烧字幕（靠提示词约束；单测必须断言请求/提示词含禁止烧字的说明）。
- 合成阶段用脚本口播（现有 `write_srt` 逻辑）硬烧。样式和位置先从参考片读取：
  - 有软字幕轨道（SRT/ASS/mov_text 等）则解析字体、字号、颜色、对齐和边距，作为硬烧样式。
  - 没有软字幕则检测画面字幕区域（上/中/下、大致边距），能定位置就用，字体可退回默认。
  - 都读不到时，才退回现在这套避让控件的底部安全样式，`method=default_safe`，**不得**对用户或卡片写成「已对齐参考字幕」。
- 样式结果写入 production（如 `subtitle_style`），包含 `method`、对齐、边距等，便于前端和测试断言。

### 4.5 Seedance 与验收口径

- 扩展 `generate_seedance_video`（及调用处）以组装 `input_contents` 与上述提示词。参考媒体需要 Seedance 能访问的 URL；本机 `127.0.0.1` 制品链接通常不可用。本轮实现请求体形状和可注入的 `media_public_base_url`（或测试用 URL），正式可访问的上传/中转可留到真实出片前再补，但必须写出 warning，不得假装已公网可达。
- 用 mock/单测验证请求体，不发真实生成。`local_ffmpeg` 无 Key 路径必须能：解析卖点（或无链接时跳过）、给出 3 个脚本并停下、选择后跑完生成/合成、写出分离音轨和字幕样式字段。

## 5. 整合设计（已锁定）

建议新增模块（名称可商榷，职责必须单一）：

- `backend/video_fission/landing.py`：URL 识别、安全抓取、卖点抽取。
- `backend/video_fission/audio_separate.py`：人声/伴奏分离。
- `backend/video_fission/subtitle_style.py`（或放在 `media.py` 但接口清晰）：参考字幕样式检测。

改动集中处：`pipeline.py`（script 结束后暂停、generate 参考音视频与口播回退、compose 只混分离 BGM、按参考样式硬烧）、`providers.py`（Seedance `input_contents`）、`productions.py` / `api/routes.py` / `tools.py`（`resume_from`、选择脚本 API）、`conversation_cards.py`、对话自动开任务（`service.py` 等：脚本后必须停）、`settings_store.py`（如 `media_public_base_url`）、前端 `VideoPlanCard.vue` 与跟进重跑逻辑（`App.vue` 等）。跟进分类不要只靠前端；后端要根据当前状态和说明决定重出脚本还是从 generate 续跑。

选择脚本 API 建议：`POST /api/productions/{id}/select-script`，body 含候选 id。需登录鉴权，与现有 productions 路由一致。选中后启动 generate。

候选结构示例：

```json
{
  "script_candidates": [
    {
      "id": "script_a",
      "title": "痛点切入",
      "angle": "先讲使用困扰再给方案",
      "script": { "total_duration_ms": 8100, "shots": [], "audio_plan": {}, "opening": {} }
    }
  ],
  "selected_script_id": null,
  "landing_page": {
    "source_url": "https://example.com/p",
    "parse_status": "ok",
    "method": "local_extract",
    "selling_points": [],
    "benefits": [],
    "audience": "",
    "promises": []
  }
}
```

三个候选的时间轴必须逐镜对齐；规范化阶段若模型输出打乱时长或拆开开头块，必须按现有开头连续块规则合并回去，并以参考时间轴为准，而不是采用被打乱的时长。

## 6. 测试

后端在现有 pytest 上加法，不得删掉 understand / auth / opening / Seedance 现有测试：

- 落地页：从 brief 抽出 URL；SSRF 样例被拒绝；静态 HTML 抽出卖点；无 URL 时管线不失败。
- 脚本：一次恰好 3 个候选；时间轴与开头块一致；文案/角度不同；脚本后状态为 `awaiting_script_choice`，没有 clips/final。
- 选择脚本后才进入 generate；「改卖点/换脚本」再出 3 个并停下；已选脚本后「再生成」从 generate 续跑。
- `resume_from` 接受 `script` 与 `generate`，非法值仍报错。
- 音轨分离：夹具混入可区分的人声与 BGM 后，两路产物存在、时长吻合、与原混音及彼此不字节级相同。
- Seedance 请求（mock）：含 `reference_video`、两路 `reference_audio`、`generate_audio=true`，prompt 含口播要求和禁止烧字；不出现真实网络出片。
- 无音轨回退：不得输出无人口播的静音成片（可在单元里断言调用了 TTS 或人声回退）。
- 合成：混的是 `reference-bgm.m4a` 而不是整段 `reference-audio.m4a`。
- 字幕样式：有软字幕时采用轨道样式；读不到时 `method=default_safe` 且不出现「已对齐参考」文案。

前端 vitest：3 个候选的展示与选中、等待选择时的文案、卖点摘要、跟进按钮/说明不误触发。需要时补隔离 e2e（5175+8001）覆盖「出 3 个 → 选 1 个 → 任务继续」，不要指到 5174。

无 Key 回归必须能走到「3 选 1」，选择后用 `local_ffmpeg` 跑完。Grok 在 5174 做实操验收，但不做真实 Seedance 出片。

## 7. 文档

- 更新 `docs/ARCHITECTURE.md`、README 中与脚本选择、落地页、参考音频、字幕硬烧相关的说明。
- Seedance 请求体变化可在 `docs/API-SEEDANCE-VIDEO.md` 补一句项目侧如何传参考音视频，不要改接口事实。
- `status/STATUS.md` 记录落地、测试结果和已知限制（含参考 URL 公网可达、reuse 原硬烧字幕、JS 落地页需粘贴正文）。
- 不要改写本任务书的锁定语义；若必须改契约，先写 STATUS 等 Grok 确认。

## 8. 分工

### Codex Bot（后端主力）

1. 落地页安全抓取与卖点抽取，接入意图/脚本/生成提示词。
2. 3 个脚本候选、规范化（锁时间轴和开头连续块）、脚本后暂停、选择脚本 API、扩展 `resume_from` 与跟进分类。
3. 人声/BGM 分离，Seedance / 生成请求接入参考视频和两路参考音频；口播回退；合成只混分离 BGM。
4. 参考字幕样式检测，合成按该样式硬烧；读不到时走安全样式且不声称已对齐。
5. 按第 6 节补后端测试并跑通 `cd /workspace/video-fission/backend && uv run pytest -q`。
6. 交叉验证 Claude 的前端：选择脚本契约、等待态、跟进重跑是否与后端状态机一致。

### Claude Bot（前端 + 交叉验证）

1. 规划卡展示落地页卖点摘要、3 个创意脚本（角度/口播摘要）和「选用」操作；等待选择时进度卡说清楚。
2. 对话跟进：改文案/卖点可再出 3 个；已选后可再生成；选中态要能看出来。不要恢复旧顶栏或能力面板。
3. 补齐 vitest；需要时补隔离 e2e，不得把官方 e2e 指到 5174。
4. 交叉验证 Codex：对照本任务书第 4–5 节读暂停/选择、分离结果、Seedance 请求体、字幕样式与口播回退，用测试或只读审查给证据，把问题写回本群。
5. 前端有改动后再视需要重启 5174；不要动 5173，不要杀 8000 除非与 Codex 约定由谁重启。

交叉验证都必须写证据（测试命令与结果、关键代码位置）。不要只说「看起来没问题」。

## 9. 验收清单

1. 对话 brief 里的落地页地址能被分析成结构化卖点，并进入创作提示词；抓取失败可粘贴正文，且不会伪装成已完整解析。SSRF 防护有效。
2. 每次脚本阶段恰好 3 个候选，时间轴和开头连续块与参考片一致，文案角度不同；任务在脚本后停下等选择。
3. 用户挑选后才生成；改说明后可再出 3 个脚本，或在已选脚本上从生成阶段重跑。
4. 参考片人声与 BGM 被分离为两路制品；生成请求（含 Seedance 请求体）带上参考视频和两路参考音频；`generate_audio=true`；提示词要求口播且禁止烧字。
5. 无音轨结果会补口播，不用静音充数。合成只混分离后的 BGM。
6. 字幕只在合成硬烧；能读到参考样式/位置就用，否则安全样式且不声称已对齐。
7. 开头连续块、卡片稳定 id、认证、Gemini、默认 `local_ffmpeg`、侧栏设置入口不被破坏。
8. pytest / vitest 按第 6 节通过。不要求真实 Seedance 出片。
9. Grok 在 `http://127.0.0.1:5174/` 做无 Key / 功能实操验收。

## 10. 进度板

- [ ] 落地页卖点与提示词
- [ ] 三脚本候选、暂停选择与再次生成
- [ ] 人声与 BGM 分离及模型参考音视频接线
- [ ] 禁止模型字幕，按参考样式硬烧
- [ ] 合成音轨与口播回退
- [ ] 后端/前端测试
- [ ] 交叉验证
- [ ] Grok 功能验收（不做出片）
