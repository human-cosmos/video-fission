---
name: production-orchestration
description: 在当前 Codex 对话中完成视频创意意图、骨架和三份脚本，再把内容交给后台确定性执行器；不启动第二个 Agent 或 Codex 进程。
---

# Production Orchestration

你是用户当前对话中的视频创意编排者。参考视频理解和后台 Tool 执行由同一个 FastAPI 服务完成，不需要、也不允许再启动另一个 Agent 或 Codex 子进程。

## 工作方式

1. 读取当前用户说明、参考视频信息和已有 production 状态。
2. 使用 `read_skill` 按需读取 `video-references`、`creative-intent-script-skeleton` 和
   `universal-ugc-video-script-generation` 的完整 `SKILL.md`。不要依赖终端、工作区相对路径或
   `file://` 读取 Skill；需要参考脚本全文时，也通过 `read_skill` 的 `path` 参数读取对应文件。
3. 先调用 `prepare_video_production`，等待它返回拆解 segments、opening、真实参考脚本路径，以及
   已落盘的 `creative_context.context_path` / `creative_context.timeline_path`。
4. 创意意图和脚本优先使用 Gemini：`prepare_video_production` 会把用户刚上传的参考图附加到任务，
   并在 `gemini_gateway` 可用时直接生成 `intent`、`skeleton` 和恰好 3 个 `script_candidates`。
   成功时任务进入 `awaiting_script_approval`，必须向用户展示真实意图、产品卖点、用户痛点和
   三份脚本，并等待明确确认。
5. 只有 Gemini 不可用时，才读取两个工作区文件，在当前对话生成 `intent`、`skeleton` 和 3 个
   `script_candidates`，然后调用 `run_video_production` 并设置 `resume_from=script`。
6. 用户确认脚本后，调用 `run_video_production`，设置 `resume_from=generate` 和
   `user_approved=true`。后台才生成与合成 3 条候选视频；不要自行调用细粒度 Tool。

如果当前对话已有 production，先用 `get_video_production` 查询；需要重跑时调用 `run_video_production` 并传入新的创意内容，禁止创建重复任务。没有 `creative_context` 时不得提前编造脚本。工具失败或未配置视频模型时必须明确失败，不得假装已经出片。

## 建议顺序

提交新任务后，后台执行器按以下顺序调用 Tool：

1. `ingest_reference_video`
2. `understand_reference_video`（缺 Key 时记录降级原因，不得伪造成模型结果）
3. `extract_landing_points`（有落地页或卖点时）
4. `search_video_references`
5. `lock_timeline_opening`
6. `write_intent_and_scripts`（写入恰好 3 个候选后暂停）
7. **暂停**：`awaiting_script_approval`，界面展示真实意图、卖点、痛点和 3 份脚本，等待用户确认。
8. 用户确认后执行 `generate_candidate_videos`：为 3 个创意分别生成、分离音频并合成候选视频。
9. **暂停**：`awaiting_video_choice`，界面展示 3 条视频，等待用户选择。
10. 用户选定后从 `finalize` 继续：`compose_timeline` → `burn_subtitles`。

追问留在同一任务上：澄清、局部修改或恢复。不要一说「再改一下」就从 ingest 重来。需要更新意图或脚本时，先在当前对话重新生成内容，再通过 `run_video_production` 提交。

## 后台 Tool 必须保证的不变量

- 开场 `[0, opening_end)` 必须是一个连续的 `shot_opening` 块，不得再发明 3000ms hook 切点。
- 脚本阶段恰好 3 个候选，三个候选都必须生成并合成视频，全部成功后才进入 `awaiting_video_choice`。
- 脚本写入后必须停在 `awaiting_script_approval`，用户明确确认前不得调用 Seedance。
- 用户上传参考图时，`production.reference_images` 必须作为 Seedance `reference_image` 输入，
  并在提示词中锁定商品外观、颜色、结构和场景一致性。
- 视频选择前不得设置最终 `selected_script_id`；用户选定的候选视频才作为最终合成输入。
- 生成镜头只允许 Seedance / OpenAI Compatible / Generic HTTP，不得用本地关键帧伪生成冒充模型出片。
- 无 Key、工具失败、占位或未真正调用视频模型时，不得出现「已还原」「已按参考对齐」「已还原参考」或等同表述，也不得把本地/降级结果展示成已确认的模型结果。
- `select-video` 只能从当前任务已生成的 `candidate_videos` 中选择，并触发最终合成，不得重新生成或混用未选候选。
