# 把 video_slot 理解与切片能力迁入 video-fission：任务书

状态：已锁定（Grok Bot，2026-11-18 09:55 Asia/Shanghai）
工作区：`/workspace/video-fission`（共享电脑）。参考实现只读：`/workspace/video_slot`。不要改用户本机副本，不要 git 提交/推送，除非 Grok 另行批准。
审批：开发中的问题和权限全部由 Grok Bot 审批。契约变更先写到 `status/STATUS.md` 并等 Grok 确认。

## 1. 目标

把 `video_slot` 中已经跑通的能力，以**源码级移植 + 适配**的方式并进 video-fission 的创作管线，替换当前偏弱的「ffmpeg 场景切分 + 模板摘要」decompose：

1. **视频检测**：TransNetV2 + PySceneDetect 做帧级转场检测，并把模型给出的时间点吸附到真实切点。
2. **整片理解**：输出 `video_summary` / `video_describe`、商品名和整片标签（场景、模特、风格、口播、BGM 等）。
3. **切片（含片段时间）**：连续、不重叠的片段，带 `start_ms` / `end_ms`（内部可保留秒，对外仍是毫秒整数），并切出可播放的片段文件。
4. **片段理解**：每段的描述、摘要、三级标签（`slot` / `sub_slot` / `sub_slot_desc`）及 `lens_info` 字段。
5. **observe**：每段观察卡 `shot_observation_card`、`raw_claims`、`speech_evidence`。

对话里现有的理解卡、片段卡必须展示这些新信息；成片管线（intent / script / generate / compose）继续可用。没有模型 Key 时，默认 `local_ffmpeg` 仍必须跑完全程。

## 2. 非目标

- 不推翻 `docs/TASKS-VIDEO-PIPELINE.md` 与 `docs/TASKS-CHAT-NATIVE-PRODUCTION.md` 已锁定的阶段顺序、`replace_policy`、对话入口和五类卡片的稳定 id。本任务只做 **decompose 增强** 与卡片/设置的加法。
- 不迁移 Flask / gunicorn（`video_slot/app.py`）、CLI 批量入口，也不在产品里保留内网默认地址作为必填配置。
- 不迁入 `guard-oss-client`、OSS / GCloud / 素材库上传。片段文件只落在 `data/artifacts/productions/{id}/`，通过已有 artifacts API 播放和下载。
- 不把 `VIDEO_SPLIT_CATEGORY_URL` 做成硬依赖。商品三级分类可后续作为可选适配器；本轮不做，字段允许为空。
- 不接入 385 任务的片头通用段判别和视频分析报告（`video_slot` 本身也未做）。
- 不做基于时长的补段/合并；沿用 video_slot 策略：采用模型时间段，只做连续性校验和帧级边界精修。
- 不引入 GPU 训练。允许**可选**接入 TransNetV2 推理权重（约 30MB，CPU 可跑），以 extra 依赖安装；缺依赖时自动退化，不得让管线失败。
- 不删除、不改写参考库 630 篇脚本。不 git 提交/推送，不写回本机，除非 Grok 批准。
- 不要动占用 `127.0.0.1:5173` 的 URRE 前端。本项目前端若需重启，使用 `5174`；后端仍是 `8000`。Playwright e2e 继续用隔离端口（5175 + API 8001）。

## 3. 源码结论（锁定依据）

### 3.1 video_slot 主链路

入口 `videosplit.split_video`（`videosplit/splitter.py`）：

准备源并校验 → 压缩到 Gemini 15MB 上限（`video_utils.ensure_under_gemini_size`）→ 上传得可被模型访问的 URL（本轮不迁上传）→ `SplitProcessor.split` 用 Gemini 一次产出整片标签 + `video_lens` → `TransitionDetector.refine_segments` 精修边界 → 可选商品类目 → `process_clips` 逐段切分并做片段解析 → `observe_clip` 写观察卡。

真实产物形状见 `/workspace/video_slot/output/video_split_20261117_200357.json`：

- 整片：`video_describe`、`video_summary`、`product_name` 及一组列表型标签。
- `video_lens[]`：`index`、`start_time`、`end_time`（秒）、`description`、`summary`、三级标签。
- `lens_info[]` 与 lens 按 index 对应，含片段描述、场景/模特/动作等，以及 `observe`。
- `observe`：`shot_observation_card`、`raw_claims`、`speech_evidence`。
- 单个片段失败时保留该段并写 `error`，其他段继续。

提示词在 `config_prompt.py`（整片拆分+标签）与 `config_observe.py`（观察卡）。模型原始字段仍是 `tag_level1/2/3`，规范化后统一为 `slot` / `sub_slot` / `sub_slot_desc`（见 `processors._normalize_tags`）。

一级标签到现有 `role` 的映射必须固定为：

| slot（一级） | role |
| --- | --- |
| 开场钩子 | hook |
| 痛点具象、方案登场、卖点展示、效果展示、场景演示、信任构建 | value |
| 响应号召 | cta |
| 其他或空 | other |

多个 `value` 段保留各自 id，供 script 复用决策使用。

### 3.2 转场检测

`TransitionDetector`（`videosplit/transition_detector.py`）：

- PySceneDetect 经 `models/shot_detector.py` 出帧分数；
- TransNetV2 经 `models/transnet_v2.py` + `models/transnetv2-pytorch-weights.pth` 出 CNN 分数；
- `refine_segments` 把模型给出的时间点吸附到最近的帧级变化点；检测失败则只做连续性校验（`_continuity_only`）。

依赖：`torch`、`opencv-python`、`numpy`、`scenedetect`，权重约 30MB。这些都是可选的。

### 3.3 模型调用与不能原样照搬的部分

`LLMClient.call_gemini_with_video` 走 Giikin 网关：先取 token，再 POST `conversationV5`，body 里用 Gemini `parts` + `fileData.fileUri`。这不是 video-fission 现有的 `{base_url}/chat/completions`。内网 OSS / 素材库 URL 在本工作区通常不可用，不能作为产品默认路径。

### 3.4 video-fission 现状

`pipeline._stage_decompose`：`media.scene_cut_ms`（ffmpeg `gt(scene,0.15)`）+ Hook/Value/CTA 窗口规则；无 Key 时摘要是结构化模板。配置了 `openai_compatible` 时，切分后再送 8–16 帧做 JSON 理解，失败则保留本地切分并 warning。

对话卡片：`video_understanding` / `video_segments` 目前只展示 `summary`、角色和时间，没有整片标签、三级标签和 observe。

## 4. 整合设计（已锁定）

### 4.1 模块落点

在 video-fission 内新建自包含包，不要运行时 import `/workspace/video_slot`：

```
backend/video_fission/understand/
  prompts.py          # 从 config_prompt.py / config_observe.py 移植，保持语义
  normalize.py        # processors 中的清洗、校验、role 映射
  transitions.py      # TransitionDetector 及 shot_detector / transnet 适配
  clips.py            # 切分到 artifacts、片段解析规范化
  observe.py          # observe 规范化与调用
  gateway.py          # 可选 gemini_gateway 客户端（可配置，无内网硬编码必填）
  weights/            # TransNetV2 权重（从 video_slot 拷入，文档说明来源）
```

提示词、规范化、精修、观察卡 schema 以 video_slot 源码为准做移植，允许为毫秒字段和 artifacts 路径做适配，禁止另起一套不兼容的标签语义。

### 4.2 Provider

继续使用设置里的 `multimodal`，扩展枚举：

- `local_ffmpeg`（默认）：能做转场检测则做，否则沿用 ffmpeg 场景切分；整片/片段理解为结构化本地描述（可含时长、切点、画幅）；`observe` 为空对象或省略，并写 warning。必须跑完管线。
- `openai_compatible`：先完成检测与切分；若接口能接受视频（压缩后 ≤ 配置上限，优先 ≤15MB），把视频交给模型跑移植后的提示词；否则退化为关键帧 + 简要音频线索，并 warning。失败不得让整个 production 失败（保留本地切分）。
- `gemini_gateway`（新增）：配置 `base_url`（对话接口）、可选 `token_url`、model、api_key。请求格式对齐 `LLMClient` 的 `messages` / `parts` / `fileUri`。若没有模型可拉取的公网 URL，先尝试网关可接受的本地/内联方式；仍不行则退化到帧并 warning。默认不填写任何内网地址。

`token_url` 锁定为 `multimodal.token_url`，字符串，默认 `""`，不是必填。仅在 `provider` 为 `gemini_gateway` 时使用：有值则先向该地址换票再调 `base_url`；留空则跳过换票，请求直接带已保存的 `api_key`。设置回读时 `token_url` 可明文返回（它是地址，不是 Key），`api_key` 仍只掩码。

设置加法（保持 Key 掩码规则）：

```json
{
  "understand": {
    "transition_detection": true,
    "clip_analysis": true,
    "observe": true,
    "clip_workers": 3,
    "gemini_max_size_mb": 15,
    "observe_timeout_seconds": 360
  }
}
```

`GET/PUT /api/settings` 与 `POST /api/settings/test` 覆盖新 provider 与这些开关。关 `clip_analysis` 则不做片段级模型解析和 observe；关 `observe` 则只保留片段标签解析。observe 调用不要套用 `multimodal.timeout_seconds` 的默认 120s，使用 `understand.observe_timeout_seconds`（默认 360）。完整远端理解+切片+observe 在约 98s 视频上可能要 11–15 分钟，前端与 SSE 必须能等完整段结束，不得按 120s 误判失败。

可选依赖写入 `pyproject.toml` extra：`shot-detect`（torch、opencv-python、numpy、scenedetect）。`uv sync --extra dev` 不得被强制安装 GPU 版 torch 而失败；缺 extra 时测试跳过或走退化路径，默认 pytest 仍须通过。

### 4.3 Production 对象加法

`current_stage` 仍为 `decompose`，内部子步骤通过 SSE `stage_started` / `stage_completed` 的 payload 说明（如 `transition` / `understand` / `refine` / `clip_analysis` / `observe`），不要新增会破坏现有枚举的顶层阶段。

`decomposition` 在现有字段上追加，旧字段保持有值：

```json
{
  "summary": "优先 video_summary，否则本地回退",
  "description": "video_describe，可空",
  "provider": "local_ffmpeg|openai_compatible|gemini_gateway",
  "model": "",
  "video_tags": {
    "product_name": [],
    "video_describe": "",
    "video_summary": "",
    "environment_categories": [],
    "environment_specific_scenes": [],
    "model_types": [],
    "model_social_role": [],
    "model_appearance_features": [],
    "style_categories": [],
    "style_details": [],
    "product_presentation_types": [],
    "product_showcase_forms": [],
    "narration_method_categories": [],
    "narration_method_details": [],
    "opening_types": [],
    "opening_forms": [],
    "narrative_structures": [],
    "rhythm_patterns": [],
    "interaction_modes": [],
    "BGM_categories": [],
    "BGM_styles": []
  },
  "segments": [
    {
      "id": "seg_01",
      "role": "hook|value|cta|other",
      "start_ms": 0,
      "end_ms": 2133,
      "summary": "",
      "description": "",
      "visual": "",
      "audio": "",
      "on_screen_text": "",
      "reuse_recommended": false,
      "slot": ["开场钩子"],
      "sub_slot": ["结果钩子"],
      "sub_slot_desc": ["结果状态特写"],
      "clip_path": "clips/seg_01.mp4",
      "clip_url": "/api/productions/{id}/artifacts/clips/seg_01.mp4",
      "lens_info": {},
      "observe": {
        "shot_observation_card": [],
        "raw_claims": [],
        "speech_evidence": {}
      },
      "error": null
    }
  ]
}
```

规则：

- 时间对外一律毫秒整数。片段仍须覆盖 `[0, duration_ms)`，允许 100ms 误差，不得重叠。video_slot 精修后相邻段常留 1 帧空隙（约 33ms），写入 production 前必须消掉空隙、拉齐首尾，不要把空洞留给前端。
- `summary` 不得为空（有模型用模型摘要，否则本地回退）。
- `lens_info` / `observe` 按段挂在 segment 上，便于卡片渲染；不要只放在无法展示的旁路文件里。`lens_info` 按 index 对齐，本身没有时间戳。
- observe 源数据使用片段局部时钟（该段从 0 起）。写入 `decomposition` 和观察卡之前，必须把 `shot_observation_card`、`raw_claims`、`speech_evidence` 里的时间加上该段 `start_ms`，全部改成原片时间轴；禁止把后段口播画在 0 秒。
- 切出的片段是 H.264 yuv420p + 原音频（有则保留），供对话内播放。
- intent/script 继续消费 `role`、`start_ms`、`end_ms`、`summary`、`reuse_recommended`。`reuse_recommended` 默认：`value` 为 true，hook/cta 为 false，brief 明确要求时仍服从既有 script 规则。

### 4.4 对话卡片加法

已有五类卡片的稳定 id 不变。upsert 内容增强，并新增一张观察卡：

| item_type | 稳定 id | 何时出现 | 内容 |
| --- | --- | --- | --- |
| `video_understanding` | `{prd_id}:understanding` | decompose 完成后 | 除摘要和参考视频外，展示 `video_describe`（若有）与关键整片标签（商品、场景、风格、口播方式等，空的不硬撑） |
| `video_segments` | `{prd_id}:segments` | decompose 完成后即可，有 clip 再补预览 | 起止时间、role、slot/sub_slot、描述/摘要、片段播放、reuse/generate 标记与勾选重跑 |
| `video_observe` | `{prd_id}:observe` | 有至少一段非空 observe 时 | payload 为 `{ production_id, status, updated_at, segments: [{ id, role, start_ms, end_ms, observe, error }] }`。按片段列出观察卡要点、关键 raw_claims、speech_evidence 文本与时间；某段失败只显示该段 error |

无 Key 或关闭 observe 时，不要插入一张空白观察卡。

## 5. 测试

- 把 video_slot 的规范化、连续性校验、role 映射、observe 规范化做成无网络单测。必须覆盖：1 帧空隙被消除；observe / raw_claims 的局部时间被映射到原片 `start_ms`。
- 转场精修：用合成分数或短 fixture 断言时间点被吸附且仍连续覆盖全时长。
- `torch` 等缺失时，相关测试 skip，decompose 走退化路径，既有全流程 pytest 必须通过。
- 无 Key 下 `POST /run` 仍产出可 ffprobe 的 `final.mp4` / `final.srt`。
- 有 decomposition 新字段的 fixture，断言理解卡/片段卡/观察卡渲染，且旧字段兼容。
- 现有对话 e2e、production e2e 不得无故删除；e2e 仍走隔离端口。
- 配置了假的远端失败时，必须 warning + 本地回退，不能整单失败。

## 6. 文档

更新 `README.md` 与 `docs/ARCHITECTURE.md`：新依赖 extra、`understand` 设置、`gemini_gateway`、无 Key 退化行为。不要写 API Key，不要把内网地址写成默认必填。

## 7. 分工

### Codex Bot（后端主力）

移植 understand 包、权重、可选依赖、settings 加法、`_stage_decompose` 重接、SSE 子步骤、artifacts 切片、pytest、README/架构中的后端部分。先打通 **无 Key 退化路径 + 规范化单测**，再接 `openai_compatible` / `gemini_gateway`。

### Claude Bot（前端 + 交叉验证）

增强 `VideoUnderstandingCard`、`VideoSegmentsCard`，新增 `VideoObserveCard`（或等价组件）并接到 `conversation_cards` upsert；设置页增加 provider 与 understand 开关；核对契约、补前端测试/e2e。不要在未开 STATUS 的情况下改字段名。

两边都在 `/workspace/video-fission` 工作，改前拉齐，避免互相覆盖。

## 8. 验收清单

1. 无任何 API Key 时，对话上传短视频 + 说明仍能跑完全程并下载可播放成片。
2. 转场检测在 extra 可用时生效；不可用时自动退化并 warning，不失败。
3. 配置可用的多模态 / `gemini_gateway` 后，对话中可见：整片理解与标签、带精确时间的切片、片段理解（含三级标签）、非空 observe 卡；片段可播放。
4. 片段时间覆盖全片、不重叠；role 映射符合 §3.1；intent/script/generate/compose 不被破坏。
5. 不依赖 OSS/素材库/内网分类接口即可完成上述能力（有模型时，除模型本身外无新硬依赖）。
6. 原有 Codex 对话与模型设置仍可用；顶栏不再出现已删除的模式按钮。
7. `uv run pytest -q` 通过；前端既有测试与隔离 e2e 通过。
8. README 说明如何安装 `shot-detect`、如何配置理解开关与网关。

## 9. 进度板

Codex 与 Claude 在 `status/STATUS.md` 记录开始、阻塞、完成。阻塞只找 Grok。Grok 负责盯盘和最终在 `http://127.0.0.1:5174/` 的实操验收。
