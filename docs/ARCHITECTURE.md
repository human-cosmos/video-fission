# Video Fission 架构（当前实现）

本文只描述本轮落地后的真实结构，不约定真出片验收。

## 总览

系统仍是 FastAPI 后端 + Vue 3 对话工作台 + SQLite WAL。用户在现有 Codex 对话里上传参考视频、发送创作说明；对话侧继续由 `CodexHub` 和已有会话 Tool 驱动。

出片不再走 `PipelineEngine` 的固定阶段循环，也不再启动第二个 Codex。当前对话 Codex 先调用 `prepare_video_production` 获取真实视频拆解和参考脚本检索结果，再生成意图、骨架和 3 个脚本候选；`run_video_production` 接收这些内容后，由 Python 后台确定性执行器调用细粒度 Tool。`ProductionService.prepare/run`、`start_from_tool`、`POST /run`、`POST /select-video` 以及会话工具 `run_video_production` 只负责状态推进和 Tool 执行，不发起模型规划。

`PipelineEngine` 仍保留切分、生成、合成等能力实现，供 Tool 调用；`PipelineEngine.run` 会直接抛错，不能再作为静默兜底入口。创意内容缺失时后台明确失败并要求先在当前对话生成，不会隐式启动新进程。

开发前端地址是 `http://127.0.0.1:5174/`。

## 出片编排

```text
当前 Codex 对话
  → prepare_video_production
    → ingest / understand / landing / search references
    → 返回 creative_context，状态 awaiting_creative_context
  → 基于真实拆解和检索结果生成 intent / skeleton / 3 个 script_candidates
  → run_video_production（resume_from=script）
    → ProductionService
      → ProductionAgent（确定性执行器，无子进程）
        1. DeterministicPlanner 按状态和 resume_from 生成步骤
        2. 记录 plan 并调用 ToolRegistry
        3. 写入 3 个脚本候选，并分别生成、分离音频、合成三条候选视频
        4. 暂停在 awaiting_video_choice
        5. 用户选定一条视频后，从 finalize 继续最终合成 / 烧字幕
```

`DeterministicPlanner` 不调用模型，只根据当前 production 状态、`resume_from` 和已保存的创意内容生成固定步骤。测试仍可注入 `FakeCodexRuntime` 验证异常计划分支，但生产默认路径不会实例化 `CodexAppServer`。

当前对话 Codex 通过服务端 `read_skill` 工具读取 `skills/production-orchestration/SKILL.md`、`video-references`、`creative-intent-script-skeleton` 和 `universal-ugc-video-script-generation`。该工具绕过受限工作区和终端，只允许读取已注册 Skill 目录内的文件。`prepare_video_production` 返回的 `creative_context` 必须包含拆解 segments、opening 和实际检索到的 reference paths，脚本生成不得早于该阶段。

## 出片 Tool

细粒度 Tool 定义在 `backend/video_fission/production_tools.py`，由 `register_production_tools` 挂到现有 `ToolRegistry`，与对话 Tool 共用注册表。

| Tool | 作用 |
| --- | --- |
| `ingest_reference_video` | 接入并探测参考视频 |
| `understand_reference_video` | 拆解参考视频并确定开场。生产路径仍走 Gemini；无 Key 或理解失败时 Tool 可做本地切分，并明确标注这是降级结果，不得写成模型理解 |
| `extract_landing_points` | 抽取落地页卖点（无 URL 不阻断） |
| `search_video_references` | 检索 `video-references` |
| `lock_timeline_opening` | 锁定共享时间轴，并将 `[0, opening_end)` 合并为单个连续 `shot_opening` |
| `write_intent_and_scripts` | 写入意图、骨架和恰好 3 个脚本候选，随后继续生成候选视频 |
| `get_production_state` | 读取状态、所选脚本和产物路径 |
| `update_production_progress` | 更新进度或状态 |
| `generate_candidate_videos` | 依次生成并合成 3 条候选视频，写入 `candidate_videos`，最后停在 `awaiting_video_choice` |
| `generate_video_shots` | 仅在已选定脚本后生成；通道只允许 `seedance` / `openai_compatible` / `generic_http`。存在 generate 镜头但缺少 `base_url` 或 API Key 时明确失败，不使用本地关键帧伪生成 |
| `separate_audio` | 分离参考人声/BGM；结果记录 `applied_to_output` |
| `compose_timeline` | FFmpeg 拼接时间线，并把可用参考 BGM 以低音量混入最终音轨。内部写入 `production_jobs`；成功后只保存合成产物，不得把任务标为 completed |
| `burn_subtitles` | 按镜头文案写 SRT 并硬烧到独立文件（如 `final-burned-*.mp4`），不覆盖合成成片。内部写入 `production_jobs`；计划含此步骤时，最终 `output.video_path` 必须指向烧录后文件。本工具自身也不把任务标为 completed |
| `handle_production_followup` | 同一 `production_id` 上处理追问。含糊的「再改一下 / 不满意」会停下来澄清，不会从 ingest 重来 |

会话入口为 `prepare_video_production` 和 `run_video_production`。旧 `start_video_production` 仅保留后端兼容，不再下发给对话 Codex。

拆解完成后，后台还会把完整片段时间轴、开场区间、逐段摘要和参考脚本路径写入当前用户工作区的
`productions/{production_id}/creative-context.json`，并生成便于阅读的 `timeline-summary.md`。
当前对话应直接读取这两个中间产物，不再为了补时间轴去查询数据库。

## 持久化作业、恢复与完成门禁

`generate_video_shots`、`compose_timeline`、`burn_subtitles` 在 Tool 内部落库到 SQLite 表 `production_jobs`。作业不是对话可见 Tool，不新增 `get_job` 轮询。幂等键为 `production_id + tool + shot_id + 脚本/镜头内容哈希`；重选脚本不得复用旧成功作业。

远端生成先提交再轮询：拿到远端 `job_id` 后立刻写入 `external_task_id`，然后才轮询/下载。进程崩溃后，若任务仍为 `running` 且作业带有可续取的远端 ID，启动恢复时只续取同一任务，不得向 Seedance 重新提交。本地 FFmpeg 类作业（复用裁剪、标准化、合成、烧字幕）不能在已死进程内续跑，按可重试处理，不得假装已成功。

`ProductionService.cancel` 在现有取消事件之外，把该生产任务未完成作业标为 `cancelled`；轮询看到取消后停止且不得下载。启动恢复由 `container.configure` 调用 `ProductionService.recover_orphaned_runs`：继续轮询可恢复的远端任务，再从中断步骤续跑确定性计划，而不是从 ingest 重来；不得把带可续取 `external_task_id` 的作业标为失败。

启动恢复还会识别旧版 `awaiting_script_choice` 且已有 3 个脚本候选的任务，将它们直接转入
`generate`，由 `generate_candidate_videos` 生成全部 3 条候选视频，避免旧任务永久停在已废弃的脚本选择点。

唯一可以把生产任务标为 `completed` 的入口是计划结束后的 `ProductionAgent._finalize`。必须同时满足：本计划必做步骤全部成功（`prepare_only` 与 `awaiting_video_choice` 暂停除外）；若计划含 `burn_subtitles`，最终成片路径必须是烧录后文件；成片存在且非空；该生产任务没有未完成或失败的相关作业。合成已有但计划中的烧字幕未跑或失败时，保持 `running` 或标 `failed`，不得 completed。

## 必须守住的不变量

这些规则写在 Tool 实现和 `production-orchestration` Skill 中，不只存在于提示词：

- 开场 `[0, opening_end)` 是一个连续的 `shot_opening` 块，不得再切出 3000ms 钩子
- 脚本阶段恰好 3 个候选；三条候选视频全部成功后才进入 `awaiting_video_choice`
- `generate_video_shots` 要求已有 `selected_script_id`，不得把未选定的 `production.script.shots` 当作已选
- 生成镜头只走远端视频模型通道
- 无 Key、Tool 失败、占位或没有真实视频模型调用时，不得出现「已还原」「已按参考对齐」「已还原参考」这类表述
- 追问留在同一条 `production_id`
- `select-video` 只接受当前任务的 `candidate_videos` 中的一项，并据此设置最终脚本、片段和成片
- `compose_timeline` / `burn_subtitles` 不得把生产任务标为 completed；完成只发生在计划结束门禁
- 计划含 `burn_subtitles` 时，最终 `output.video_path` 必须是烧录后文件，不能是烧录前的合成文件
- 远端生成的 `external_task_id` 必须在提交后立刻落库；崩溃恢复只续取，不重提

## 状态、事件与前端衔接

生产任务增加 JSON 字段 `agent_trace`（确定性计划、已加载 Skill、已执行 Tool）。执行器还会写入生产事件 `agent_plan`、`skill_loaded`、`tool_started`、`tool_completed`。

Codex app-server 的审批请求由后端立即以 `accept` 回应并记为 `approval.auto_approved`，不再进入
`pending_approvals`，因此前端不会展示人工审批卡。历史遗留的 pending 审批在对话加载时也会自动
批准，避免旧任务卡住。

`GET /api/productions/{id}` 的公开载荷包含 `agent_trace`。对话卡片 `video_plan` / `video_progress` 的 `plan_payload` / `progress_payload` 也附带该字段，供现有时间线展示，不新增卡片类型，也不恢复旧 UI chrome。

## 关键模块

| 路径 | 职责 |
| --- | --- |
| `backend/video_fission/production_agent.py` | 确定性执行器、计划生成、完成门禁与测试用 Fake Runtime |
| `backend/video_fission/jobs.py` | 内部作业执行器：幂等、取消检查与产物校验 |
| `backend/video_fission/production_tools.py` | 出片 Tool 与不变量校验 |
| `backend/video_fission/productions.py` | HTTP / 会话入口、创意内容持久化、取消作业与启动恢复 |
| `backend/video_fission/pipeline.py` | 能力实现；生成镜头在提交后立即持久化远端任务 ID；`run` 不再作为编排入口 |
| `backend/video_fission/tools.py` | 对话 Tool 与出片 Tool 的注册 |
| `backend/video_fission/app_server.py` | Codex app-server JSON-RPC |
| `backend/video_fission/conversation_cards.py` | 现有出片卡片的附加 `agent_trace` |
| `skills/production-orchestration/SKILL.md` | 编排顺序、暂停点与不变量 |

## 测试与范围

本轮测试在 `backend/tests/test_production_agent.py`、`test_production_tools.py` 与 `test_production_jobs.py`。官方夹具 `backend/tests/fixtures/sample-reference.mp4` 从历史提交恢复，仅供 FFmpeg 相关 Tool 使用；`725ecc9` 删除的整套历史测试没有整包恢复。

命令：

```bash
cd backend && uv run pytest -q
```

测试默认使用 `DeterministicPlanner`，不启动真实 `codex`，也不调用 Gemini / Seedance。`backend/tests/test_live_providers.py` 在 `RUN_LIVE_PROVIDERS=1` 时提供真实 Gemini / Seedance 探活；`frontend/e2e/live-5174.spec.js` 在 `RUN_LIVE_5174=1` 时检查正在运行的 5174 登录与工作台。常规 E2E 继续使用 mock 视频 Provider，避免产生外部模型费用。

常规验收不会调用外部模型；真实探活通过显式环境变量启用。前端在现有 `video_plan` / `video_progress` 卡片上展示编排计划与 Tool/Skill 执行轨迹，不新增卡片类型，也不恢复旧 UI chrome。
