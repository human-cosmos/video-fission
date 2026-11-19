# Video Fission 架构（当前实现）

本文只描述本轮落地后的真实结构，不约定真出片验收。

## 总览

系统仍是 FastAPI 后端 + Vue 3 对话工作台 + SQLite WAL。用户在现有 Codex 对话里上传参考视频、发送创作说明；对话侧继续由 `CodexHub` 和已有会话 Tool 驱动。

出片不再走 `PipelineEngine` 的固定阶段循环。`ProductionService.run`、`start_from_tool`、`POST /run`、`POST /select-script` 以及会话工具 `start_video_production` / `run_video_production` 都会把任务交给任务级 **Production Agent**。该 Agent 先给出计划、加载 Skill，再调用细粒度 Tool。它复用 `CodexAppServer` 与 Skill `extraRoots`，但不会再启动 `VideoScriptAgent` 或第二个 Codex 运行时。

`PipelineEngine` 仍保留切分、生成、合成等能力实现，供 Tool 调用；`PipelineEngine.run` 会直接抛错，不能再作为静默兜底入口。Codex 不可用时任务以明确错误失败，不会伪装已经出片。

开发前端地址是 `http://127.0.0.1:5174/`。

## 出片编排

```text
对话 / HTTP
  → ProductionService.run / select_script / start_from_tool
    → ProductionAgent
        1. Runtime.plan（真实 Codex 或测试用 Fake）
        2. 记录 plan，加载 Skill
        3. 按步骤调用 ToolRegistry 中的出片 Tool
        4. 三个脚本候选写入后暂停在 awaiting_script_choice
        5. 选定脚本后再生成 / 分离音频 / 合成 / 烧字幕
```

真实运行时 `RealCodexRuntime` 拉起一次隔离的 `CodexAppServer` 回合，要求返回包含 `skills`、`steps` 以及可选 `intent` / `script_candidates` 的 JSON 计划，再由 Python 执行 Tool。测试运行时 `FakeCodexRuntime` 注入脚本化计划，不启动 `codex` 二进制，也不调用 Gemini / Seedance。

编排约定写在 `skills/production-orchestration/SKILL.md`：计划顺序、暂停点，以及下方不变量。Production Agent 还会加载既有的 `video-references`、`creative-intent-script-skeleton`、`universal-ugc-video-script-generation`。

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
| `separate_audio` | 分离参考音频；占位或本地结果须注明 |
| `compose_timeline` | FFmpeg 拼接时间线 |
| `burn_subtitles` | 按镜头文案写 SRT 并硬烧；未对齐时使用安全默认位置并注明 |
| `handle_production_followup` | 同一 `production_id` 上处理追问。含糊的「再改一下 / 不满意」会停下来澄清，不会从 ingest 重来 |

会话入口 `start_video_production` / `run_video_production` 仍可用，语义改为把任务交给 Production Agent 执行或恢复。

## 必须守住的不变量

这些规则写在 Tool 实现和 `production-orchestration` Skill 中，不只存在于提示词：

- 开场 `[0, opening_end)` 是一个连续的 `shot_opening` 块，不得再切出 3000ms 钩子
- 脚本阶段恰好 3 个候选，状态进入 `awaiting_script_choice`；聊天自动开跑不得代为选定或为了跑完而丢掉口播
- `generate_video_shots` 要求已有 `selected_script_id`，不得把未选定的 `production.script.shots` 当作已选
- 生成镜头只走远端视频模型通道
- 无 Key、Tool 失败、占位或没有真实视频模型调用时，不得出现「已还原」「已按参考对齐」「已还原参考」这类表述
- 追问留在同一条 `production_id`
- `select-script` 只记录选择；生成需再进入 Agent，不得在选定前偷偷开始生成

## 状态、事件与前端衔接

生产任务增加 JSON 字段 `agent_trace`（计划、已加载 Skill、已执行 Tool）。Agent 还会写入生产事件 `agent_plan`、`skill_loaded`、`tool_started`、`tool_completed`。

`GET /api/productions/{id}` 的公开载荷包含 `agent_trace`。对话卡片 `video_plan` / `video_progress` 的 `plan_payload` / `progress_payload` 也附带该字段，供现有时间线展示，不新增卡片类型，也不恢复旧 UI chrome。

## 关键模块

| 路径 | 职责 |
| --- | --- |
| `backend/video_fission/production_agent.py` | Production Agent，以及 Fake / Real Codex 运行时 |
| `backend/video_fission/production_tools.py` | 出片 Tool 与不变量校验 |
| `backend/video_fission/productions.py` | HTTP / 会话入口，编排改走 Agent |
| `backend/video_fission/pipeline.py` | 能力实现；`run` 不再作为编排入口 |
| `backend/video_fission/tools.py` | 对话 Tool 与出片 Tool 的注册 |
| `backend/video_fission/app_server.py` | Codex app-server JSON-RPC |
| `backend/video_fission/script_agent.py` | 历史意图/脚本隔离 Agent；本轮出片编排不再调用它 |
| `backend/video_fission/conversation_cards.py` | 现有出片卡片的附加 `agent_trace` |
| `skills/production-orchestration/SKILL.md` | 编排顺序、暂停点与不变量 |

## 测试与范围

本轮测试在 `backend/tests/test_production_agent.py` 与 `test_production_tools.py`。官方夹具 `backend/tests/fixtures/sample-reference.mp4` 从历史提交恢复，仅供 FFmpeg 相关 Tool 使用；`725ecc9` 删除的整套历史测试没有整包恢复。

命令：

```bash
cd backend && uv run pytest -q
```

测试注入 Fake Codex，不启动真实 `codex`，也不调用 Gemini / Seedance。无理解模型时，理解 Tool 走诚实本地切分；无视频模型 Key 且存在 generate 镜头时，生成 Tool 必须明确失败。

本轮验收不包括真实 Gemini / Seedance 出片，也不包括 5174 完整链路或官方 Playwright 指向 5174。前端已在现有 `video_plan` / `video_progress` 卡片上展示编排计划与 Tool/Skill 执行轨迹，不新增卡片类型，也不恢复旧 UI chrome。
