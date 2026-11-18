# video_slot → video-fission：检测 / 理解 / 切片 / 观察 源码分析与迁移设计

日期：2026-11-18（Asia/Shanghai）
范围：只读对比 `/workspace/video_slot` 与 `/workspace/video-fission` 源码；本文件为分析简报。
配套任务书：`docs/TASKS-SLOT-UNDERSTANDING.md`（已锁定）。本文补充源码事实、真实产物形状、缺口与风险；实施以任务书为准，若与本文冲突，以源码事实修正实施细节，契约变更仍须走 STATUS。
约束：不修改任何产品 Python/JS 代码；不 git 提交。

---

## A. video_slot 管线（逐步，含函数与字段）

入口有两套，核心都调用 `videosplit.split_video`（`videosplit/splitter.py`）：

- CLI：`main.py` → `split_video(...)` → `SplitResult.to_dict()` 写 JSON。
- HTTP：`app.py` `POST /analyze`（Flask），`video_url` 必填，`only_split=true` 时关闭片段解析和 observe（`clip_analysis=not only_split`，`observe=not only_split`）。客户端示例是 `api_client.py`。

`split_video()` 逐步：

1. `_prepare_source`：本地路径或 `file://` 直接用；否则 `video_utils.download_video` 拉到本次 `run_dir`（`tempfile.mkdtemp`，在 `DEFAULT_WORK_DIR` 的按日目录下）。
2. `video_utils.validate_video` ← `probe_video`（OpenCV）：校验 fps ∈ `[FPS_MIN, FPS_MAX]` 默认 20–60、时长 ≤ `MAX_VIDEO_DURATION` 默认 600s、体积 ≤ `MAX_VIDEO_SIZE_MB` 默认 500MB。
3. `video_utils.ensure_under_gemini_size`：超过 `GEMINI_VIDEO_MAX_SIZE_MB`（默认 15MB）则 ffmpeg 压码率，最多 3 次，码率夹在 200–3000 kbps，除数 `GEMINI_COMPRESS_BITRATE_DIVISOR` 默认 13.0。
4. `LLMClient.get_access_token`：GET `VIDEO_SPLIT_TOKEN_URL`（默认 `https://idc-ai.giikin.com/api/common/get_access_token`），读 `data.client_token`，缓存约 55 分钟。失败则整段失败（“无法调用 Gemini”）。
5. `uploader.upload_for_gemini`：`prefer=auto|oss|gcloud`。auto 先 OSS（`guard_oss_client.OSSClient`），失败回退 GCloud（`VIDEO_SPLIT_GCLOUD_URL`，`verify=False`）。得到可供网关读取的 `upload_url`。
6. 若 `transition_detection=True`：`TransitionDetector.analyze(local_path)` 得到 `TransitionInfo`（失败则 `ok=False`，后续只用 Gemini 时间点）。
7. `SplitProcessor.split`（`videosplit/processors.py`）：
   - `build_prompt(duration)` 把 `config_prompt.prompt_video_split` 的 `{{VIDEO_DURATION}}` 换成总时长文本；
   - `LLMClient.call_gemini_with_video(upload_url, prompt, ...)`，最多 `GEMINI_RETRY_TIMES`（默认 2）次，按覆盖完整度打分选最佳候选（`_candidate_score` / `_is_good_enough`：偏好均长约 8s、末段贴近片尾、单段 <18s）；
   - `extract_json` → `_raw_segments`（兼容顶层数组或 `video_lens` / `data` / `result`）→ `normalize_segments` → `validate_segments`；
   - `extract_video_tags` 抽出整片标签；
   - 若 `info.ok`：`TransitionDetector.refine_segments` 后再 `validate_segments`。
8. `category.get_product_category`：用识别出的 `product_name`（或调用方传入的商品名）POST `VIDEO_SPLIT_CATEGORY_URL`，写入 `product_category` / `product_category_score`。失败返回 `("", 0.0)`，不中断。
9. 若 `clip_analysis=True`：`clip_processor.process_clips` 按段 ffmpeg 切片、上传、片段级 Gemini、可选 `observe_clip`。该步失败只打印错误，`lens_info=[]`，已完成的整片拆分保留。
10. `SplitResult.to_dict()` 组装输出；`keep_temp=False` 时清理临时目录。

时间字段在 slot 内部是**秒、三位小数**。`validate_segments` 会把第一段 `start_time` 置 0、最后一段 `end_time` 置 duration，并按 `previous_end + 1/fps` 推下一段起点，因此精修后相邻段之间常见 **1 帧空隙**（30fps 约 33ms），不是严格相接。

### A.1 当前代码的输出形状

`SplitResult.to_dict()`（`splitter.py`）顶层字段：

```
source, upload_url, upload_channel, fps, duration, segment_count,
<video_tags 展开在顶层>,
video_lens, lens_info,
meta: { elapsed_seconds, model, product_name, transition_detection, compressed }
```

整片标签字段（`processors._VIDEO_TAG_LIST_FIELDS` + 文本字段 + 类目）：

- 列表：`product_name`, `environment_categories`, `environment_specific_scenes`, `model_types`, `model_social_role`, `model_appearance_features`, `style_categories`, `style_details`, `product_presentation_types`, `product_showcase_forms`, `narration_method_categories`, `narration_method_details`, `opening_types`, `opening_forms`, `narrative_structures`, `rhythm_patterns`, `interaction_modes`, `BGM_categories`, `BGM_styles`
- 文本：`video_describe`, `video_summary`
- 后写：`product_category`, `product_category_score`

`video_lens[]` 经 `validate_segments` 后为：

```
index, start_time, end_time, description, summary, slot, sub_slot, sub_slot_desc
```

`slot` / `sub_slot` / `sub_slot_desc` 都是字符串列表。提示词仍要求模型输出 `tag_level1/2/3`（一级一个字符串、二级一个字符串、三级 1–3 个）；`processors._normalize_tags` 兼容两套名字，输出统一为列表，且 `sub_slot_desc` 最多 3 个。

`lens_info[]` 与 `video_lens` **按 index 对齐，自身不含 start_time/end_time**。字段见 §D。

### A.2 真实产物摘录

当前契约的完整样例：`/workspace/video_slot/output/api/video_analysis_20261117_230937_ccd097fa.json`（该文件无 Flask 包一层，直接是 `to_dict()` 结果）。约 98.467s、30fps、8 段、8 条 `lens_info`（均含 observe），`upload_channel=oss`，`meta.model=gemini-3.0-flash`，`elapsed_seconds=660.54`，`transition_detection=true`，`compressed=false`，`product_name=["三色遮瑕膏"]`，`product_category="底妆"`，`product_category_score=1.0`。

`video_lens[0]` 原文：

```json
{
  "index": 1,
  "start_time": 0.0,
  "end_time": 6.2,
  "description": "模特在梳妆台前，用指尖蘸取遮瑕膏点涂在脸部瑕疵处，镜头展示了遮瑕盘的外观及其三个颜色的质地，随后通过特写展示了遮瑕后的皮肤状态。",
  "summary": "产品亮相及即时遮瑕效果",
  "slot": ["开场钩子"],
  "sub_slot": ["结果钩子"],
  "sub_slot_desc": ["结果状态特写", "前后对比"]
}
```

全片时间轴（注意 1 帧空隙）：

```
1: 0.0–6.2        slot=["开场钩子"]   sub=["结果钩子"]
2: 6.233–15.167   slot=["信任构建"]   sub=["暴力实测"]
3: 15.2–24.3      slot=["痛点具象"]   sub=["身体问题"]
4: 24.333–35.267  slot=["卖点展示"]   sub=["便捷用法"]
5: 35.3–45.233    slot=["效果展示"]   sub=["结果状态"]
6: 45.266–60.567  slot=["信任构建"]   sub=["承诺保障"]
7: 60.6–72.233    slot=["卖点展示"]   sub=["材质成分"]
8: 72.266–98.467  slot=["响应号召"]   sub=["价格锚点"]
```

同文件 `lens_info[0]` 关键字段（观察卡见 §D）：`index=1`，`url` 为素材库 mp4，`md5`，`error=""`，`is_include_product="是"`，`clip_description`，以及 `style_categories` / `environment_category` / `model_type` / `displayed_*` / `slot` 等；**没有时间字段**。

更早的批跑结果（如 `output/eval_mp4/*.json`、`output/video_split_20261117_200357.json`）的 `video_lens` 仍是 `tag_level1/tag_level2/tag_level3`，且多数没有 `lens_info`。那是规范化改名之前的产物，不能当作当前代码的输出契约。`video_split_20261117_200357.json` 也是 98.467s 片，但拆成 13 段、耗时 551.7s，说明同一条视频的段数不稳定，依赖模型候选与是否启用片段阶段。

仅拆分（`only_split` / `--no-clip-analysis`）的 API 样例约 94–265s；完整「切片 + 片段理解 + observe」约 660–900s（11–15 分钟）。

---

## B. 转场检测：TransNetV2 + PySceneDetect

实现：`videosplit/transition_detector.py` 的 `TransitionDetector`。

### 检测

- `analyze(video_path)`：
  - 构造时尝试 `from .models.shot_detector import ShotDetector`。导入失败（缺 `scenedetect` 等）则打印“跳过 PySceneDetect 转场检测”，返回 `TransitionInfo(ok=False)`。**整条拆分不会因此失败。**
  - `ShotDetector.get_frame_scores`（`models/shot_detector.py`）：OpenCV 读 fps/帧数；`scenedetect.ContentDetector(threshold=27.0, min_scene_len=1)` + `StatsManager` 取每帧 `content_val`。
  - 再懒加载 `models.transnet_v2.TransNetV2Model.predict_video`。失败只记“TransNetV2 推理失败”，只要 frame_scores 或 cnn_scores 非空，`ok` 仍可为 True。
- `TransNetV2Model`（`models/transnet_v2.py`）：
  - 权重默认 `videosplit/models/transnetv2-pytorch-weights.pth`（约 30MB）。`torch.load(..., map_location="cpu")`。
  - `_predict_raw(..., device=torch.device("cpu"))`：**写死 CPU，不需要 GPU。**
  - 用 `ffmpeg-python` 抽出全部帧，缩到 `48x27` rgb24，滑动窗口 100 帧、步长 50，sigmoid 得单帧转场分数。
  - `torch.set_num_threads(config.THREAD_NUM)` 默认 2。

依赖（`requirements.txt`）：`torch>=2.6.0`、`opencv-python`、`numpy`、`ffmpeg-python`、`scenedetect>=0.7.0`。ffmpeg 二进制也必须在 PATH。权重需随代码分发或单独安装，不在 git LFS 说明里强制。

### 精修

`refine_segments(info, segments)` 把 Gemini 给出的边界**吸附**到附近帧级变化点，而不是单独用检测器切语义段：

- 窗口：段长 ≤2s 时在 `seg_end ± 1s`，否则 `± 2s`。
- 若窗口内传统 `content_val` 最大值 ≥ 35：用该帧时间，不裁剪。
- 否则用 CNN 分数峰值；若峰值 `> 0.3`，`need_cut=True`，再减 `10/fps`（约 10 帧）避免切到转场帧上。
- 两路分数都为空时走 `_continuity_only`：只保证首段从 0 起、末段到 duration、相邻连续。
- 第一段 start 强制 0，最后一段 end 强制 duration。

结论：转场检测是**可选精修器**，语义切分仍由模型完成。缺依赖或推理失败必须降级，不能变成硬失败。

---

## C. Gemini 如何被调用（上传、15MB、提示词）

### 调用路径（不可原样搬进 video-fission）

`videosplit/llm_client.py`：

- `call_gemini_with_video` 组装的不是 OpenAI `chat/completions`，而是 Gemini 风格：

```
messages: [{ role: "user", parts: [
  { fileData: { mimeType: "video/mp4", fileUri: <已上传 URL> } },
  { text: prompt }
]}]
```

- POST `VIDEO_SPLIT_GEMINI_URL`（默认 `https://openapi.giikin.com/ai/gen/v1/chat/conversationV5`），header `Authorization: <token>`（无 Bearer 前缀），另含 `model_engine`（默认 `gemini-3.0-flash`）、`source=eval_hot_video_structure`、`max_tokens * 30`、`temperature`（拆分 0.6，observe 0.2）。
- 视频必须先变成网关能拉取的 HTTP URL（OSS / GCloud）。本地文件不能直接作为 `fileUri`。

上传详见 `videosplit/uploader.py`，片段另有 `MATERIAL_UPLOAD_URL`（`clip_processor._upload_material`）写 `lens_info.url`，失败则回退到 Gemini 上传 URL。

### 提示词（只概括用途与约束，不整段粘贴）

`config_prompt.prompt_video_split`（`build_prompt` 填时长）：

- 角色：电商广告整片理解 + 语义分镜。
- 切分依据：镜头/场景切换、卖点或主题变化、广告结构变化；禁止固定间隔机械切；常规 5–15s，单段建议 ≤20s、≥3s；钩子段建议 ≤10s。
- 时间：MM:SS 或一位小数；必须从 00:00 覆盖到总时长，相邻 `end_time == next.start_time`，禁止重叠或空隙。
- 三级标签体系（一级职责 → 二级类型 → 三级画面证据）。一级固定 8 类：开场钩子、痛点具象、方案登场、卖点展示、效果展示、场景演示、信任构建、响应号召。模型字段名是 `tag_level1/2/3`。
- 同时输出 §A.1 全部视频级标签；部分字段枚举封闭（如 `environment_categories` 6 类、`style_categories` 6 类、`narration_method_categories` 4 类等）。
- `video_describe` 只用画面、≥100 字；`video_summary` ≤150 字，可结合音频。
- 只输出一个 JSON 对象，不要 markdown。

`config_prompt.prompt_lens_analysis`（`build_lens_prompt` 填 `{{PRODUCT_CLAUSE}}`）：

- 输入是**已切开的单段视频**。
- 输出：`is_include_product`（"是"/"否"）、`clip_description`（画面、≥30 字，不用音频）、以及场景/模特/动作/道具/功能/痛点等列表字段。封闭枚举不得自造。
- 只依据当前片段，禁止串入其他段内容。

`config_observe.PROMPT_LENS_OBSERVE`：

- 规范名 `video-observation` / 契约 `video_observation.v1`。只记可见、可听、可读事实，不做专业标签、效果评价或拍摄建议。
- 占位符 `__TASK_JSON__` 由 `observe._build_task` 替换：整段剪辑视为一个 range 单元，`shot_id=1`，`time_origin=provided_clip`，带 `original_offset_seconds`，`review` 固定 `{"status":"not_requested"}`，禁止拆镜、禁止改时间轴。
- 描述性文字用中文；`speech_transcript`、画面原文、`raw_claim` 保持原语言。
- 模型应输出 `shot_observation_card`、`speech_evidence`（以及规范里的更完整顶层字段；运行时只收这几个）。

---

## D. 切片、片段理解、observe 字段

`clip_processor.process_clips`（默认并发 `CLIP_THREAD_NUM=3`，API 用 `API_CLIP_THREAD_NUM=6`）：

1. `_cut_clip`：ffmpeg `-ss start -t duration -c:v libx264 -preset veryfast -c:a aac`，超时 `CLIP_CUT_TIMEOUT` 默认 300s。
2. 片段再次 `ensure_under_gemini_size`。
3. `_file_md5`；`_upload_material`（可失败）；`upload_for_gemini` 供模型读。
4. `_annotate_clip` → `_normalize_clip_result`。
5. 若 `observe=True`：`observe.observe_clip`；失败写 `empty_observe()`，不影响已有片段标签。单段异常写 `error`，其他段继续。

`lens_info` 一项的字段（`_empty_result` + 解析结果 + 可选 observe）：

```
index, url, md5, error,
is_include_product, clip_description,
style_categories, style_details,
environment_category, specific_scene,
model_type, model_social_role, model_appearance_feature,
displayed_body_parts, displayed_human_actions, displayed_items, auxiliary_items,
season, festival_promotion, displayed_functions, user_pain_points,
slot, sub_slot, sub_slot_desc,   # 从对应 video_lens 继承
observe?
```

`observe` 三件套（`empty_observe` / `observe_clip`）：

- `shot_observation_card[]`：一张卡（整段一个单元）。`_CARD_DEFAULTS` 字段包括：`shot_id`, `start_time`, `end_time`, `evidence_coverage{visual,audio,notes}`, `visual_subjects`, `visual_actions`, `setting_and_props`, `camera_observation`, `lighting_and_image_observation`, `graphic_text_observation`, `language_and_locale_observation`, `information_presentation_observation`, `speech_transcript`, `audio_observation`, `sequence_function_observation`, `raw_claim_id`, `raw_claim`, `raw_claim_source`, `raw_claim_scope`, `raw_claim_alignment`, `raw_claim_language`, `ambient_raw_claims`, `claim_expression`, `review_questions`（最多 2 条）, `uncertainty_notes`, `review`。
- `raw_claims[]`：由卡片主主张与 `ambient_raw_claims` 汇总，含 `raw_claim_id/raw_claim/source_type/scope/language/shot_refs/start_time/end_time/usage`（`primary|ambient|both`）。
- `speech_evidence`：`status`, `source`, `language`, `segments[]`（`segment_index/start/end/text`）, `notes`。

**时钟陷阱（实施必须处理）：** `observe_clip` 把卡的时间写成片段局部时钟（`start_time=0`, `end_time=clip_duration`），`original_offset_seconds` 只在 prompt 的 task JSON 里。真实样例：第 1 段卡为 `0.0–6.2`（恰好等于原片）；第 2 段 `video_lens` 为 `6.233–15.167`，卡与 `raw_claims` 却是 `0.0–8.934`；第 8 段原片 `72.266–98.467`，卡为 `0.0–26.201`。迁入 video-fission 后必须把观察时间映射回 `start_ms/end_ms`（或同时保留 `clip_local` 与 `original`），否则对话卡会把后段口播画在 0 秒。

`ffprobe` 探测无音轨时，`_apply_audio_probe` 把 `evidence_coverage.audio` 从 `unavailable` 改成 `full` 并注明“已确认无音轨”。observe 调用最多 2 次（缺卡则补看一次），超时默认 360s。

---

## E. 不能盲目拷进 video-fission 的外部/内部依赖

| 依赖 | 位置 | 原因 |
| --- | --- | --- |
| `guard-oss-client` + 内网 PyPI | `uploader._upload_to_oss`，`requirements.txt` 注释 | 私有包，工作区不可用 |
| GCloud 上传 `VIDEO_SPLIT_GCLOUD_URL` | `uploader._upload_to_gcloud` | 内网 openapi，且 `verify=False` |
| 素材库 `VIDEO_SPLIT_MATERIAL_URL` | `_upload_material` | `lens_info.url` 专用，非产品存储 |
| Token `VIDEO_SPLIT_TOKEN_URL` | `LLMClient.get_access_token` | Giikin IDC，无此令牌则整条链路失败 |
| Gemini 网关 `VIDEO_SPLIT_GEMINI_URL` + `parts/fileData` | `llm_client.py` | 不是现有 `{base_url}/chat/completions` |
| 商品类目 `VIDEO_SPLIT_CATEGORY_URL` | `videosplit/category.py` | 内网分类；本轮任务书已定为非目标 |
| Flask + gunicorn | `app.py`, `start_cmd.sh server`，端口写死 8008 | video-fission 已是 FastAPI |
| CLI 批量 / 默认绝对路径视频 | `main.py` `DEFAULT_VIDEO`，`start_cmd.sh` `EVAL_DIR` | 数据集与运维入口，不是产品 UX |
| 385 任务报告 / 片头通用段判别 | 注释中“对应 385 任务”；代码未实现报告 | 任务书已排除 |
| 硬编码内网默认 URL | `config.py` 全部默认值 | 不得写进产品必填配置 |

可迁移的是：提示词语义、JSON 规范化、连续性校验、转场精修算法、切片命令、observe schema 与本地 artifacts 化存储。

---

## F. video-fission 当前 decompose

阶段锁在 `PipelineEngine.STAGES`：`ingest → decompose → intent → script → generate → compose`（`pipeline.py`）。默认提供者 `local_ffmpeg`，无 Key 也能跑完全程（`docs/ARCHITECTURE.md`、`docs/TASKS-VIDEO-PIPELINE.md`）。

`_stage_decompose`：

1. `media.probe`（ffprobe）得 `duration_ms/width/height/fps/has_audio`。
2. `media.scene_cut_ms`：ffmpeg `select='gt(scene,0.15)',showinfo`，解析 `pts_time`，单位毫秒。超时 60s。
3. 有音轨则 `silence_points_ms`（`silencedetect=noise=-30dB:d=0.18`）。
4. `plan_segments(duration_ms, cuts, silence, has_audio)`：用片头/片尾窗 `min(3000, max(1500, 15%))` 定 hook/cta 目标，优先吸附窗内静音点（±200ms）或任意场景切点；中间切点不足时每 4s 补一刀。角色规则：第一段 `hook`，最后一段强制 `cta`，中间全是 `value`。官方夹具切在 2700/5400ms，测试断言三段 hook/value/cta（`backend/tests/test_pipeline.py::test_local_decompose_covers_timeline_with_hook_value_cta` 与 `test_sample_reference_detects_main_scene_cuts`）。
5. 每段抽 3 帧（15%/50%/82%），文件名 `{seg_id}_frame_0N.jpg`，**不把帧 URL 写入契约字段**。无模型时 `summary/visual/audio` 是模板句（“HOOK 段，时长 …ms。场景切点划分。”）。
6. 若设置 `multimodal.provider == "openai_compatible"` 且有 key 与 `base_url`：`_refine_with_multimodal` 只把最多 16 张 `seg_*_frame_01.jpg`（每段第一张）以 data-URL 图片发给 `providers.chat_completions`（`POST {base_url}/chat/completions`）。提示词很短，只要 `role∈{hook,value,cta,other}` 的 JSON。失败保留本地切分并 `warning`。
7. `normalize_model_segments` 排序、消重叠、把首段拉到 0、末段拉到 `duration_ms`（100ms 容差），并保证至少有 hook 与 cta。

契约段字段（`docs/TASKS-VIDEO-PIPELINE.md`）：`id, role, start_ms, end_ms, summary, visual, audio, on_screen_text, reuse_recommended`。时间一律毫秒整数；片段须覆盖 `[0, duration_ms)`，允许 100ms 误差，不得重叠。

对话卡（`conversation_cards.py` + 前端）：

- `video_understanding`：摘要、参考视频、按 role + `start_ms/end_ms` + summary 列段。
- `video_segments`：同样的时间/角色，加上 reuse/generate、关键帧、勾选重跑。
- 没有整片标签、三级 slot、片段播放文件（decompose 阶段不切预览 mp4；`cut_segment` 用在 generate 的 reuse）、也没有 observe。
- `docs/TASKS-CHAT-NATIVE-PRODUCTION.md`：五类卡片稳定 id 不可推翻；只允许加法。

相对 video_slot 的弱点：

- 切分是场景差 + 固定 Hook/Value/CTA 时间窗，不是广告语义结构（8 类一级职责）。
- 无模型时摘要是模板，不含画面/口播事实。
- 有模型时也**看不到视频和音频**，只看每段一张关键帧，且 refine 提示不含 slot 体系。
- 不做逐段切片理解，不做观察卡 / raw_claims / 口播证据。
- 边界没有帧级 CNN/ContentDetector 精修（ffmpeg scene 阈值 0.15 与 PySceneDetect 27.0 也不是同一量纲）。

---

## G. 缺口：迁什么、改什么、留下什么

### 应迁移（语义与算法）

- `prompt_video_split` / `prompt_lens_analysis` / `PROMPT_LENS_OBSERVE` 的任务定义、标签枚举、输出 schema。
- `extract_json`、`normalize_segments`、`validate_segments`、`extract_video_tags`、`_normalize_tags`、片段结果与 observe 规范化。
- `TransitionDetector.refine_segments` + ShotDetector/TransNet（作为**可选 extra**）。
- 按已确定时间轴 ffmpeg 切片（输出到 productions artifacts，而不是素材库）。
- observe 三件套字段及“单段失败不丢整单”的容错。
- 一级 slot → 现有 `role` 的固定映射（与任务书 §3.1 一致）：

| slot | role |
| --- | --- |
| 开场钩子 | hook |
| 痛点具象、方案登场、卖点展示、效果展示、场景演示、信任构建 | value |
| 响应号召 | cta |
| 其他或空 | other |

允许多个 value 段，供 script / replace_policy 使用。`reuse_recommended` 默认仍是 `value==true`。

### 应适配（不能照搬）

- 时间对外毫秒整数；内部可用秒。把 slot 的 1 帧空隙吸附为连续、不重叠，并满足现有 100ms 覆盖容差。
- observe / speech / raw_claims 的片段局部时钟 → 原片 `start_ms/end_ms`。
- 模型访问改走 settings 里的 multimodal，不写死 Giikin URL；视频文件落在 `data/artifacts/productions/{id}/`，用已有 artifacts API 播放。
- `lens_info` 按段挂到 `decomposition.segments[i]`，不要只保留一个对不齐时间的平行数组。
- `_stage_decompose` 内部可增加 SSE 子步骤（transition / understand / refine / clip_analysis / observe），**不要新增顶层 stage**，以免破坏 `resume_from` 与卡片时机。
- 卡片只做加法：增强 understanding/segments，有非空 observe 时再出现观察卡；无 Key 或关闭 observe 不插空卡。
- 官方 8.1s 夹具上的 `plan_segments(8100, [2700,5400])` 行为，在无模型默认路径上必须保持，避免炸掉现有 pytest。

### 应放弃

- Flask/gunicorn、`api_client.py`、`start_cmd.sh` 运维入口。
- OSS / GCloud / 素材库上传，`guard-oss-client`。
- 商品类目 HTTP（字段可空，本轮不做适配器）。
- 385 报告。
- 运行时 import `/workspace/video_slot`。
- 把内网地址写成产品默认必填。
- 引入 GPU 训练或把 torch 变成默认必装依赖。

---

## H. 推荐整合设计（贴合 FastAPI + SQLite productions）

与已锁定的 `docs/TASKS-SLOT-UNDERSTANDING.md` §4 对齐，并补充源码级实施要点。

### 模块

在包内新建自包含 `backend/video_fission/understand/`（prompts / normalize / transitions / clips / observe / 可选 gateway），权重从 video_slot 拷入并注明来源。不要改 intent/script/generate/compose 的阶段语义。

### Provider 映射

继续用 `SettingsStore` 的 `multimodal`，枚举扩展为：

- `local_ffmpeg`（默认，无 Key 必须跑通）：
  - 若 `shot-detect` extra 可用且 `understand.transition_detection=true`，可用 TransNet/PySceneDetect **辅助切点**；不可用则保持现有 `scene_cut_ms` + `plan_segments`。
  - 无模型时不要假装写出 slot/observe；`summary` 用本地回退，`observe` 省略，可 warning。
  - 仍产出 hook/value/cta、覆盖全片、可进入后续阶段并出片。
- `openai_compatible`：先本地检测/切分（或在能送视频时让模型直接出 `video_lens`）。现有 `chat_completions` **只支持图文**，不能假设所有兼容网关都接受 `fileUri`。若兼容接口能收视频（压缩后优先 ≤ `gemini_max_size_mb`，默认 15），再用移植提示词；否则退化为更密的关键帧 + 可选音频线索，并 warning。远端失败不得让 production 失败。
- `gemini_gateway`（新增、可选）：请求格式对齐 `LLMClient` 的 `parts` + `fileUri`，但 **base_url / token URL / key 全由用户配置，默认空**。没有模型可拉取的公网 URL 时，先试网关能接受的本地/内联方式，再退化到帧。不要把 Giikin 主机写成默认值。

不建议把“官方 Google GenAI SDK”作为本轮必做；若后续要加，应是另一个显式 provider，同样不能替换无 Key 默认路径。

设置加法（任务书已锁定示例）：`understand.transition_detection / clip_analysis / observe / clip_workers / gemini_max_size_mb`。关 `clip_analysis` 则不做片段级模型与 observe；关 `observe` 只保留片段标签。

可选依赖：`pyproject.toml` extra `shot-detect`（torch、opencv-python、numpy、scenedetect）。`uv sync --extra dev` 不得被强制安装 GPU 版 torch 而失败。

### 扩展 decomposition schema（加法，旧字段保持有值）

```json
{
  "summary": "优先 video_summary，否则本地回退（不得为空）",
  "description": "video_describe，可空",
  "provider": "local_ffmpeg|openai_compatible|gemini_gateway",
  "model": "",
  "video_tags": { "...见任务书 §4.3，含 product_name / video_describe / video_summary 与各列表标签" },
  "segments": [
    {
      "id": "seg_01",
      "role": "hook|value|cta|other",
      "start_ms": 0,
      "end_ms": 6200,
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

建议在写入 observe 前，把卡/主张/口播时间加上该段 `start_ms`（或增加明确的 `time_origin` 字段）。`clip_*` 使用 yuv420p H.264 + 原音频，供对话播放。intent/script 继续只依赖旧字段。

### 建议开发顺序（供 Codex / Claude 交叉验证）

1. Codex：`understand/normalize.py` + slot→role + 连续性/毫秒换算单测；无 Key 退化路径先绿。
2. Codex：可选 `transitions` + extra；缺 torch 时 skip/降级；不得改变默认夹具测试。
3. Codex：重接 `_stage_decompose`、artifacts 切片、settings 加法、SSE 子步骤；再接 `openai_compatible` / 可选 `gemini_gateway`。
4. Claude：理解卡/片段卡加法、`video_observe` 新卡、设置页开关；接到真实 `conversation_cards` upsert，不用假数据冒充完成。
5. 双方交叉验证：无 Key 出片、远端失败回退、时间覆盖与 role 映射、旧 e2e 仍过、不写内网默认值。

盯盘与实操验收按任务书 §8–§9，状态写 `status/STATUS.md`。

---

## I. 风险

- **依赖体积与安装**：torch + opencv + scenedetect 很重；权重 30MB。必须 extra 化。CPU 推理在长视频上可能耗时数十秒到数分钟，需在 SSE 中可见，避免像卡死。
- **无 Key 回退**：不能因为缺模型、缺权重或网关失败而让 decompose/production 失败。默认路径必须仍产出可播放 `final.mp4` / `final.srt`。
- **耗时**：video_slot 完整链路在 ~98s 视频上实测 11–15 分钟（多次 Gemini + 并发切片 + observe）。video-fission 的 multimodal 超时默认 120s，observe 在 slot 里单独 360s。需要提高可配超时，并允许关闭 observe。
- **重叠/空隙规则**：slot 精修后常有 1 帧空隙；fission 契约禁止重叠、要求覆盖全片（100ms 容差）。归一化时要显式消除空隙，不要把 33ms 空洞留给前端。
- **观察时钟**：不映射回原片时间会造成严重展示错误（见 §D）。
- **现有测试**：`plan_segments` 与 `scene_cut_ms==[2700,5400]`、全流程出片、chat-native 卡片稳定 id、replace_policy。语义切分若在无模型路径上替换时间窗规则，会大面积失败。有模型路径也必须仍保证 hook+cta 存在，否则 `local_intent` / script 会缺骨架。
- **模型接口不匹配**：把 Giikin `fileUri` 请求直接打到 OpenAI 兼容接口会失败；必须按 provider 分支，并准备帧回退。
- **提示词体积**：observe 提示词很长，加上视频，token/超时风险高；保持“最多补看一次”和单段容错。
- **并发**：片段线程数要可配，避免拖死 FastAPI 所在进程；默认跟随 slot 的 3。
- **安全**：不要把 token、内网 URL、OSS 域名写进默认设置或 README。

---

## 附录：关键路径速查

| 能力 | video_slot | video-fission 现状 |
| --- | --- | --- |
| 编排 | `videosplit.splitter.split_video` | `pipeline.PipelineEngine._stage_decompose` |
| 场景/转场 | `TransitionDetector` + TransNet/PySceneDetect | `media.scene_cut_ms` + `plan_segments` |
| 整片理解 | `SplitProcessor.split` + `prompt_video_split` | 模板摘要，或最多 16 张首帧的短 JSON refine |
| 时间归一 | `validate_segments`（秒，可留 1 帧空隙） | `normalize_model_segments`（毫秒，消重叠、拉齐首尾） |
| 切片 | `clip_processor._cut_clip` | `media.cut_segment`（generate reuse 才切） |
| 片段理解 | `prompt_lens_analysis` → `lens_info` | 无 |
| 观察 | `observe.observe_clip` | 无 |
| 模型 | Giikin Gemini `fileUri` | `providers.chat_completions` 图文 |
| 存储 | OSS/素材库 URL + 本地 JSON | SQLite production + artifacts 目录 |
