# 状态：持久化作业与完成门禁

- 更新时间：2026-11-19 13:47 Asia/Shanghai
- 任务书：`/workspace/TASKS-JOBS-AND-COMPLETION-GATE.md`（已锁定）
- 仓库：Windows 本机 `C:\Users\Administrator\Desktop\video-fission`（PC-20251023ORRU），HEAD `ae5eaf7`，未 git 提交
- 未改工作区 `/workspace/video-fission`，未启动 8000 / 5174，未调用真实 Gemini / Seedance，未配置 Key
- 结论：交叉验证通过，无阻塞缺陷。收口交给 Grok Bot

## 交叉验证（Claude，2026-11-19 13:47 Asia/Shanghai）

在同一 Windows 仓库独立复跑，并对照任务书第 4 节抽查实现。

```powershell
cd C:\Users\Administrator\Desktop\video-fission\backend
uv run pytest -q
# 39 passed, 2 skipped in 203.11s (0:03:23)
# 时间：2026-11-19 13:43 Asia/Shanghai（Claude 独立复跑，非转述）
```

对照第 4 节：

1. `compose_timeline` / `burn_subtitles` 成功后 production 仍为 `running`；`completed` 只在 `ProductionAgent._finalize`（`production_agent.py` 第 316-354 行）。计划含烧字幕时，最终 `output.video_path` 必须等于烧录产物 `final-burned-*.mp4`（`production_tools.py` 第 423-430、448-485 行）。对应 `test_compose_and_burn_only_agent_completes`。
2. 计划含 `burn_subtitles` 但未跑、失败、缺文件、空文件或旧合成文件时不得 `completed`。对应 `test_no_false_completion`。
3. Seedance / 通用通道在拿到 `job_id`/`task_id` 后立刻经 `on_submitted` 写入 `external_task_id`，再轮询（`providers.py` 第 286-290、424-430 行；`pipeline.py` 第 271-272 行）。崩溃后 `recover_production_jobs` 不把带远端 ID 的 running job 改失败，恢复只续轮询同一 ID，不再提交。对应 `test_submit_id_persisted_then_crash_resume_and_idempotency`、`test_startup_recovers_remote_then_remaining_plan`。
4. 同幂等键且产物合格则跳过；脚本重选后换键不复用。对应上述续取测试第二次 `_stage_generate` 与 `test_script_reselection_never_reuses_old_job`。
5. `ProductionService.cancel` 将未完成 job 标 `cancelled`（`productions.py` 第 445-457 行；`db.py` 第 1261-1266 行）；轮询看到取消后停止且不下载。已成功 job 不变。对应 `test_cancel_jobs_and_stop_poll_without_download`。
6. 启动恢复走 `container.configure` → `productions.recover_orphaned_runs`（`container.py` 第 48 行）：本地无远端 ID 的 running job 改回 `pending` 可重试；有 `external_task_id` 的保持 running 后续取。Agent `resume_from=recover` 从已成功步骤前缀续跑，不从头 ingest（`production_agent.py` 第 171-188、232 行）。对应 `test_recovery_continues_interrupted_step_without_ingest`。
7. `PipelineEngine.run()` 仍直接抛错（`pipeline.py` 第 62-70 行）。三脚本暂停、未选脚本不能生成、开场锁定等原有守门回归仍在 `test_production_agent.py` / `test_production_tools.py`。默认生成计划仍含 `burn_subtitles`（`test_default_plan_keeps_required_subtitles`）。未新增对话侧 `get_job`，未恢复 `VideoScriptAgent`。

结论：第 4 节开发 + 单测 + 交叉验证通过，无阻塞缺陷。本轮不做真出片，也不做 5174 完整链路。未 git 提交，未改工作区仓库。

## 此前记录

## 本轮实现（Codex，2026-11-19 13:40 Asia/Shanghai）

在 Tool 内部补上 `production_jobs` 持久化作业与启动恢复，并把 `completed` 收敛到计划结束后的唯一门禁：

- 公开 Tool 名不变；`generate_video_shots` / `compose_timeline` / `burn_subtitles` 经 `jobs.JobRunner` 落库。作业不是对话可见 Tool，未新增 `get_job` 轮询，未恢复 `VideoScriptAgent`。
- 远端生成拿到 `job_id` 后立刻写入 `external_task_id`，再轮询/下载。崩溃后只续取同一远端任务，不重新提交。本地 FFmpeg 类作业按可重试处理，不假装已成功。
- `compose_timeline` 与 `burn_subtitles` 自身不把生产任务标为 completed。烧字幕写到独立的 `final-burned-*.mp4`，不覆盖合成文件。计划含烧字幕时，最终 `output.video_path` 必须指向烧录后文件。
- `ProductionService.cancel` 会把未完成作业标为 `cancelled`；轮询看到取消后停止且不下载。`container.configure` 调用 `recover_orphaned_runs`：续取可恢复的远端作业，再从中断步骤续跑确定性计划；不把带 `external_task_id` 的作业标失败。
- `PipelineEngine.run()` 继续直接抛错。

本机验证：

```powershell
cd C:\Users\Administrator\Desktop\video-fission\backend
uv run pytest -q
# 39 passed, 2 skipped in 201.01s (0:03:21)
# 时间：2026-11-19 13:39 Asia/Shanghai
```

回归覆盖原有 `test_production_agent.py` / `test_production_tools.py`，新增 `backend/tests/test_production_jobs.py`，覆盖任务书第 4 节：合成不得提前完成、缺烧录不得 completed、提交后落库再崩溃只续取、同幂等键跳过、取消停止轮询、启动恢复不误杀可续取远端作业，以及默认计划仍含烧字幕。

## 此前状态

# 状态：Codex Agent 编排出片（已收口）

- 最新架构：2026-11-19 单 Codex 编排改造已完成。当前对话生成 `intent` / `skeleton` / 3 个 `script_candidates`，`ProductionAgent` 改为后台确定性执行器，不再实例化 `CodexAppServer` 或启动第二个 Codex 子进程。
- 最新验证：完整后端 `uv run pytest -q` 为 `22 passed, 2 skipped`；生产主流程和脚本三选一 Playwright 均通过；前端 `45 passed` 且生产构建成功；相关 Ruff 检查通过。
- 风险修复（2026-11-19 晚）：新增 `prepare_video_production` 两阶段上下文链路；`separate_audio` 的参考 BGM 已实际混入最终音轨；删除 `VideoScriptAgent`、`_stage_intent` 和 `_stage_script` 历史路径；新增 opt-in 真实 Gemini/Seedance 探活和 5174 浏览器冒烟测试。
- 更新时间：2026-11-19 11:11 Asia/Shanghai
- 任务书：`docs/TASKS-CODEX-AGENT-ORCHESTRATION.md`（已锁定）
- 基线：`725ecc9`，未 git 提交，未改本机仓库，未配置 Key，未启动或重启 8000 / 5174，未碰 5173
- 验收口径：开发 + 单测 + 交叉验证；不做 Gemini/Seedance 真出片或 5174 完整链路验证
- 结论：本轮开发收口通过。Grok Bot 已独立复跑测试并抽查任务书第 4–5 节，与 Claude Bot 交叉验证结论一致。

## 收口（Grok，2026-11-19 11:11 Asia/Shanghai）

独立复跑（非转述）：

```bash
npm test --prefix frontend
# Test Files  5 passed (5)
# Tests  45 passed (45)
# Duration  175ms
# 时间：2026-11-19 11:10 Asia/Shanghai

cd /workspace/video-fission/backend && uv run pytest -q
# 16 passed, 1 warning in 35.77s
# 时间：2026-11-19 11:11 Asia/Shanghai
```

抽查确认：`ProductionService.run` / `_run_thread` 走 `_orchestrator().run`（`productions.py` 第 298、645 行）；`PipelineEngine.run` 直接抛错（`pipeline.py` 第 66–74 行）；`start_video_production` / `run_video_production` 文案为交给 Production Agent（`tools.py` 第 358、389 行）；`select_script` 写入 `selected_script_id` 后 `run(..., resume_from="generate")`（`productions.py` 第 364–373 行）。开场锁定、恰好 3 个候选并暂停、无 Key / 无 Codex 明确失败、追问留在同一 `production_id`，均与 Tool 实现和单测一致。前端轨迹接到现有 `video_plan` / `video_progress`，无新卡片类型，未恢复 CapabilityPanel。

非阻塞残留：`container.py` 第 40–41 行在 `allow_local_copy_fallback` 为假时仍会构造 `VideoScriptAgent` 并挂到 `PipelineEngine`；当前编排路径不会调用它，`PipelineEngine.run` 也不能再作为静默兜底。

未 git 提交，未写回本机。本轮收口不等于真出片或 5174 完整链路验收。

## 前端与交叉验证（Claude，2026-11-19 11:09 Asia/Shanghai）

前端已把 Production Agent 的编排计划和 Tool/Skill 轨迹接到现有 `video_plan` / `video_progress` 卡片，不新增卡片类型，不恢复 CapabilityPanel / 旧顶栏。新增 `frontend/src/utils/agentTraceDisplay.js` 与单测；`VideoPlanCard.vue` 展示编排计划（Skill 与工具步骤），`VideoProgressCard.vue` 展示执行轨迹。

验证命令与结果：

```bash
npm install --prefix frontend
# added 69 packages，found 0 vulnerabilities

npm test --prefix frontend
# Test Files  5 passed (5)
# Tests  45 passed (45)
# Duration  175ms
# 时间：2026-11-19 11:08 Asia/Shanghai

cd /workspace/video-fission/backend && uv run pytest -q
# 16 passed, 1 warning in 31.16s
# 时间：2026-11-19 11:09 Asia/Shanghai（Claude 独立复跑，非转述）
```

对照任务书第 4–5 节：

1. 出片入口走 Production Agent，不走阶段循环。`ProductionService.run` 调用 `_orchestrator().run`（`backend/video_fission/productions.py` 第 298、645 行）。`PipelineEngine.run` 直接抛错（`pipeline.py` 第 66–74 行）。会话工具 `start_video_production` / `run_video_production` 文案为「已交给 Production Agent」（`tools.py` 第 358、389 行）。`select_script` 先写入 `selected_script_id`，再 `run(..., resume_from="generate")`（`productions.py` 第 364–373 行）。对应测试：`test_run_goes_through_production_agent_not_pipeline_loop`、`test_start_and_run_tools_hand_off_to_agent`、`test_pipeline_engine_run_is_no_longer_an_entry`。
2. 可注入桩：`FakeCodexRuntime`（`production_agent.py`）。`test_fake_runtime_never_needs_codex_binary` 确认不启动真实 `codex`。同一套测试覆盖：先计划再加载 Skill 再调 Tool；未选定脚本不会进入 `generate_video_shots`；开场由 `lock_timeline_opening` / `assert_opening_locked` 锁成单个 `shot_opening`（`production_tools.py` 第 90–112、以及第 263–304 行恰好 3 个候选并暂停）；Codex 不可用时失败文案含「Codex 不可用」（`production_agent.py` 第 36、236–238 行）；无 Key 时 `generate_video_shots` 明确失败且不含「已还原」「已按参考对齐」「已还原参考」（`production_tools.py` 第 350–368 行）；追问留在同一 `production_id`（`handle_production_followup`，`production_tools.py` 第 457–488 行）。
3. Tool 可直接单测，见 `backend/tests/test_production_tools.py`。FFmpeg 相关使用从 `1750dfe` 恢复的 `backend/tests/fixtures/sample-reference.mp4`。
4. 前端 `npm test --prefix frontend`：45 passed。
5. 本交叉验证基于独立复跑命令与代码抽查，不是「看起来没问题」。
6. README 前端地址已改为 `http://127.0.0.1:5174/`。`docs/ARCHITECTURE.md` 已改到与当前前端展示一致（去掉「后续再改前端」的过时表述）。

结论：第 4–5 节开发 + 单测 + 交叉验证通过，无阻塞缺陷。本轮不做真出片，也不做 5174 完整链路。未 git 提交，未写回本机。

非阻塞残留：`container.py` 第 40–41 行在 `allow_local_copy_fallback` 为假时仍会构造 `VideoScriptAgent` 并挂到 `PipelineEngine`；`_stage_intent` / `_stage_script` 仍在 `pipeline.py` 中。当前 Production Agent 与出片 Tool 不会调用这两段，因此不构成套娃执行，但启动时仍会创建该对象。

## 此前记录
- 更新时间：2026-11-19 10:48 Asia/Shanghai
- 任务书：`docs/TASKS-CODEX-AGENT-ORCHESTRATION.md`（已锁定）
- 基线：`725ecc9`，未 git 提交，未改本机仓库，未配置 Key，未启动或重启 8000 / 5174，未改 `frontend/`，未碰 5173
- 验收口径：开发 + 单测 + 交叉验证；不做 Gemini/Seedance 真出片或 5174 完整链路验证
- 分工：Codex Bot 后端已落地；接下来由 Claude Bot 把计划 / Tool / Skill 轨迹接到现有卡片并做交叉验证；Grok Bot 盯盘与收口

## 后端落地（Codex，2026-11-19 10:48 Asia/Shanghai）

Production Agent、细粒度出片 Tool、run / 选脚本 / 追问接线、单测、`docs/ARCHITECTURE.md` 和 README 已完成。`PipelineEngine.run` 不再作为执行入口。

验证命令与结果：

```bash
cd /workspace/video-fission/backend && uv run pytest -q
# 16 passed, 1 warning in 29.80s
```

单测文件：`backend/tests/test_production_agent.py`、`backend/tests/test_production_tools.py`。覆盖点包括：`ProductionService.run` / `start_video_production` / `run_video_production` / `select_script` 走 Agent 而不是阶段循环；Fake Codex 先计划再加载 Skill 再调 Tool；未选定脚本不会生成；开场由 Tool 锁成单个 `shot_opening`；Codex 不可用时明确失败；无视频模型 Key 且存在 generate 镜头时生成 Tool 明确失败，无「已还原」「已按参考对齐」等禁止表述；追问留在同一 `production_id`；FFmpeg 分离 / 合成 / 硬烧使用官方夹具。

夹具说明：`725ecc9` 删除了历史 `backend/tests`。本轮只从 `1750dfe` 恢复了 `backend/tests/fixtures/sample-reference.mp4`（ffprobe 时长 8.100s），供 FFmpeg 相关 Tool 测试，没有整包恢复旧测试套件。

实现要点：

- 新文件：`backend/video_fission/production_agent.py`、`production_tools.py`，以及 `skills/production-orchestration/SKILL.md`
- 无 Gemini Key 时，`understand_reference_video` 会诚实降级为本地切分并标注 `degraded`，不是模型理解
- 任务公开载荷和 `video_plan` / `video_progress` 卡片已附带 `agent_trace`；事件含 `agent_plan`、`skill_loaded`、`tool_started`、`tool_completed`
- 无 Key 的生成失败或本地降级，不能当作视觉还原已验收

8000 / 5174 本轮未启动。前端展示与独立交叉验证交给 Claude。

## 此前记录（2026-11-19 10:36 Asia/Shanghai）

当时任务书已锁定、基线 `725ecc9`，后端尚未开始代码实施。前端盘点见下。

## 前端盘点（Claude，2026-11-19 10:36 Asia/Shanghai）

已读锁定任务书，基线 `725ecc9`。按建议顺序，先不改前端。

现有消息时间线已能展示对话侧 `plan`（PLAN / 计划）、`executionGroup`（执行轨迹，含 `dynamicToolCall` / `mcpToolCall` / 命令等）和推理轨迹，出片侧仍用 `video_progress` / `video_plan` 等现有卡片。后续若要展示 Production Agent 的计划与 Tool/Skill 调用，将接到这些已有类型上，不恢复 CapabilityPanel / 旧顶栏，也不无故新增卡片类型。`TRACE_ITEM_TYPES` 是否需要纳入 Skill 加载事件，等后端给出事件形状后再补。

工作区当前只有 URRE 的 5173（pid 3699806）在听；8000 / 5174 未启动。未改代码，未配 Key，未 git 提交。后端有可跑测试后再做交叉验证。
