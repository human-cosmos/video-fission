---
name: production-orchestration
description: 在当前 Codex 对话中完成视频创意意图、骨架和三份脚本，再把内容交给后台确定性执行器；不启动第二个 Agent 或 Codex 进程。
---

# Production Orchestration

你是用户当前对话中的视频创意编排者。参考视频理解和后台 Tool 执行由同一个 FastAPI 服务完成，不需要、也不允许再启动另一个 Agent 或 Codex 子进程。

## 工作方式

1. 读取当前用户说明、参考视频信息和已有 production 状态。
2. 按需加载 `video-references`、`creative-intent-script-skeleton` 和 `universal-ugc-video-script-generation`。
3. 先调用 `prepare_video_production`，等待它返回拆解 segments、opening 和真实参考脚本路径。
4. 基于 `creative_context` 在当前对话中生成 `intent`、`skeleton` 和恰好 3 个 `script_candidates`。
5. 调用 `run_video_production`，传入 `production_id` 和创意内容，并设置 `resume_from=script`。
6. 后台确定性执行器会锁定时间轴、写入脚本并暂停等待选择；不要自行再调用细粒度 Tool，也不要把任务交给另一个模型进程。

如果当前对话已有 production，先用 `get_video_production` 查询；需要重跑时调用 `run_video_production` 并传入新的创意内容，禁止创建重复任务。没有 `creative_context` 时不得提前编造脚本。工具失败或未配置视频模型时必须明确失败，不得假装已经出片。

## 建议顺序

提交新任务后，后台执行器按以下顺序调用 Tool：

1. `ingest_reference_video`
2. `understand_reference_video`（缺 Key 时记录降级原因，不得伪造成模型结果）
3. `extract_landing_points`（有落地页或卖点时）
4. `search_video_references`
5. `lock_timeline_opening`
6. `write_intent_and_scripts`（加载创意 Skill 后写入恰好 3 个候选）
7. **暂停**：`awaiting_script_choice`，等待用户选择。当前对话不得自动选定脚本，也不得为了跑通丢掉或替换配音。
8. 用户选定后：`generate_video_shots` → `separate_audio` → `compose_timeline` → `burn_subtitles`。

追问留在同一任务上：澄清、局部修改或恢复。不要一说「再改一下」就从 ingest 重来。需要更新意图或脚本时，先在当前对话重新生成内容，再通过 `run_video_production` 提交。

## 后台 Tool 必须保证的不变量

- 开场 `[0, opening_end)` 必须是一个连续的 `shot_opening` 块，不得再发明 3000ms hook 切点。
- 脚本阶段恰好 3 个候选，并在 `awaiting_script_choice` 暂停。
- 没有 `selected_script_id` 时禁止进入 `generate_video_shots`；不得把尚未选定的 `production.script.shots` 当成已选。
- 生成镜头只允许 Seedance / OpenAI Compatible / Generic HTTP，不得用本地关键帧伪生成冒充模型出片。
- 无 Key、工具失败、占位或未真正调用视频模型时，不得出现「已还原」「已按参考对齐」「已还原参考」或等同表述，也不得把本地/降级结果展示成已确认的模型结果。
- `select-script` 在未选定脚本时不得偷偷开始生成。
