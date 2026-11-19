# Video Fission 架构（当前实现）

本文只描述本轮落地后的真实结构，不约定真出片验收。

## 总览

系统仍是 FastAPI 后端 + Vue 3 对话工作台 + SQLite WAL。用户在现有 Codex 对话里上传参考视频、发送创作说明；对话侧继续由 `CodexHub` 和已有会话 Tool 驱动。

出片不再走 `PipelineEngine` 的固定阶段循环，也不再启动第二个 Codex。当前对话 Codex 先调用 `prepare_video_production` 获取真实视频拆解和参考脚本检索结果，再生成意图、骨架和 3 个脚本候选；`run_video_production` 接收这些内容后，由 Python 后台确定性执行器调用细粒度 Tool。`ProductionService.prepare/run`、`start_from_tool`、`POST /run`、`POST /select-script` 以及会话工具 `run_video_production` 只负责状态推进和 Tool 执行，不发起模型规划。

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
        3. 写入 3 个脚本候选后暂停在 awaiting_script_choice
        4. 选定脚本后生成 / 分离音频 / 合成 / 烧字幕
```

`DeterministicPlanner` 不调用模型，只根据当前 production 状态、`resume_from` 和已保存的创意内容生成固定步骤。测试仍可注入 `FakeCodexRuntime` 验证异常计划分支，但生产默认路径不会实例化 `CodexAppServer`。

当前对话 Codex 加载 `skills/production-orchestration/SKILL.md`、`video-references`、`creative-intent-script-skeleton` 和 `universal-ugc-video-script-generation`。`prepare_video_production` 返回的 `creative_context` 必须包含拆解 segments、opening 和实际检索到的 reference paths，脚本生成不得早于该阶段。

## 出片 Tool

细粒度 Tool 定义在 `backend/video_fission/production_tools.py`，由 `register_production_tools` 挂到现有 `ToolRegistry`，与对话 Tool 共用注册表。

| Tool | 作用 |
| --- | --- |
| `ingest_reference_video` | 接入并探测参考视频 |
| `understand_reference_video` | 拆解参考视频并确定开场。生产路径仍走 Gemini；无 Key 或理解失败时 Tool 可做本地切分，并明确标注这是降级结果，不得写成模型理解 |
| `extract_landing_points` | 抽取落地页卖点（无 URL 不阻断） |
| `search_video_references` | 检索 `video-references` |
| `lock_timeline_opening` | 锁定共享时间轴，并将 `[0, opening_end)` 合并为单个连续 `shot_opening` |
| `write_intent_and_scripts` | 写入意图、骨架和恰好 3 个脚本候选，然后暂停等待选择；不会自动选定 |
| `get_production_state` | 读取状态、所选脚本和产物路径 |
| `update_production_progress` | 更新进度或状态 |
| `generate_video_shots` | 仅在已选定脚本后生成；通道只允许 `seedance` / `openai_compatible` / `generic_http`。存在 generate 镜头但缺少 `base_url` 或 API Key 时明确失败，不使用本地关键帧伪生成 |
| `separate_audio` | 分离参考人声/BGM；结果记录 `applied_to_output` |
| `compose_timeline` | FFmpeg 拼接时间线，并把可用参考 BGM 以低音量混入最终音轨 |
| `burn_subtitles` | 按镜头文案写 SRT 并硬烧；未对齐时使用安全默认位置并注明 |
| `handle_production_followup` | 同一 `production_id` 上处理追问。含糊的「再改一下 / 不满意」会停下来澄清，不会从 ingest 重来 |

会话入口为 `prepare_video_production` 和 `run_video_production`。旧 `start_video_production` 仅保留后端兼容，不再下发给对话 Codex。

## 必须守住的不变量

这些规则写在 Tool 实现和 `production-orchestration` Skill 中，不只存在于提示词：

- 开场 `[0, opening_end)` 是一个连续的 `shot_opening` 块，不得再切出 3000ms 钩子
- 脚本阶段恰好 3 个候选，状态进入 `awaiting_script_choice`；聊天自动开跑不得代为选定或为了跑完而丢掉口播
- `generate_video_shots` 要求已有 `selected_script_id`，不得把未选定的 `production.script.shots` 当作已选
- 生成镜头只走远端视频模型通道
- 无 Key、Tool 失败、占位或没有真实视频模型调用时，不得出现「已还原」「已按参考对齐」「已还原参考」这类表述
- 追问留在同一条 `production_id`
- `select-script` 只记录选择；生成需再进入后台执行器，不得在选定前偷偷开始生成

## 状态、事件与前端衔接

生产任务增加 JSON 字段 `agent_trace`（确定性计划、已加载 Skill、已执行 Tool）。执行器还会写入生产事件 `agent_plan`、`skill_loaded`、`tool_started`、`tool_completed`。

`GET /api/productions/{id}` 的公开载荷包含 `agent_trace`。对话卡片 `video_plan` / `video_progress` 的 `plan_payload` / `progress_payload` 也附带该字段，供现有时间线展示，不新增卡片类型，也不恢复旧 UI chrome。

## 关键模块

| 路径 | 职责 |
| --- | --- |
| `backend/video_fission/production_agent.py` | 确定性执行器、计划生成与测试用 Fake Runtime |
| `backend/video_fission/production_tools.py` | 出片 Tool 与不变量校验 |
| `backend/video_fission/productions.py` | HTTP / 会话入口、创意内容持久化与后台执行 |
| `backend/video_fission/pipeline.py` | 能力实现；`run` 不再作为编排入口 |
| `backend/video_fission/tools.py` | 对话 Tool 与出片 Tool 的注册 |
| `backend/video_fission/app_server.py` | Codex app-server JSON-RPC |
| `backend/video_fission/conversation_cards.py` | 现有出片卡片的附加 `agent_trace` |
| `skills/production-orchestration/SKILL.md` | 编排顺序、暂停点与不变量 |

## 测试与范围

本轮测试在 `backend/tests/test_production_agent.py` 与 `test_production_tools.py`。官方夹具 `backend/tests/fixtures/sample-reference.mp4` 从历史提交恢复，仅供 FFmpeg 相关 Tool 使用；`725ecc9` 删除的整套历史测试没有整包恢复。

命令：

```bash
cd backend && uv run pytest -q
```

测试默认使用 `DeterministicPlanner`，不启动真实 `codex`，也不调用 Gemini / Seedance。`backend/tests/test_live_providers.py` 在 `RUN_LIVE_PROVIDERS=1` 时提供真实 Gemini / Seedance 探活；`frontend/e2e/live-5174.spec.js` 在 `RUN_LIVE_5174=1` 时检查正在运行的 5174 登录与工作台。常规 E2E 继续使用 mock 视频 Provider，避免产生外部模型费用。

常规验收不会调用外部模型；真实探活通过显式环境变量启用。前端在现有 `video_plan` / `video_progress` 卡片上展示编排计划与 Tool/Skill 执行轨迹，不新增卡片类型，也不恢复旧 UI chrome。
