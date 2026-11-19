const TOOL_LABELS = {
  prepare_video_production: '准备视频创作',
  run_video_production: '提交视频创作',
  workspace_snapshot: '查看工作区',
  read_workspace_file: '读取工作区文件',
  ingest_reference_video: '接入参考视频',
  understand_reference_video: '理解参考视频',
  extract_landing_points: '抽取落地页卖点',
  search_video_references: '检索参考脚本',
  lock_timeline_opening: '锁定开场连续块',
  write_intent_and_scripts: '写意图与三脚本',
  get_production_state: '查询任务状态',
  update_production_progress: '更新任务进度',
  generate_video_shots: '生成镜头',
  separate_audio: '分离音频',
  compose_timeline: '合成时间线',
  burn_subtitles: '硬烧字幕',
  handle_production_followup: '处理追问',
}

const SKILL_LABELS = {
  'production-orchestration': '出片编排',
  'video-references': '参考脚本',
  'creative-intent-script-skeleton': '创意意图与骨架',
  'universal-ugc-video-script-generation': '通用脚本生成',
}

export function agentTraceOf(payload) {
  const list = payload?.agent_trace
  if (!Array.isArray(list)) return []
  return list.filter((item) => item && typeof item === 'object')
}

export function hasAgentTrace(payload) {
  return agentTraceOf(payload).length > 0
}

export function latestAgentPlan(payload) {
  const plans = agentTraceOf(payload).filter((item) => item.type === 'plan')
  return plans.at(-1) || null
}

export function agentPlanSkills(plan) {
  return stringList(plan?.skills)
}

export function agentPlanTools(plan) {
  return stringList(plan?.steps)
}

export function toolLabel(name) {
  const key = String(name || '').trim()
  return TOOL_LABELS[key] || key
}

export function skillLabel(name) {
  const key = String(name || '').trim()
  return SKILL_LABELS[key] || key
}

export function agentTraceItems(payload) {
  const trace = agentTraceOf(payload)
  const rows = []
  const planIndex = trace.findLastIndex((item) => item.type === 'plan')

  if (planIndex >= 0) {
    const plan = trace[planIndex]
    const tools = agentPlanTools(plan)
    rows.push({
      key: `plan-${planIndex}`,
      kind: 'plan',
      label: '编排计划',
      detail: tools.length ? tools.map(toolLabel).join(' → ') : '尚未列出步骤',
      skills: agentPlanSkills(plan).map((name) => ({ name, label: skillLabel(name) })),
      tools,
      state: 'plan',
    })
  }

  const seenSkills = new Set()
  for (const item of trace) {
    if (item.type !== 'skill') continue
    const name = String(item.name || '').trim()
    if (!name || seenSkills.has(name)) continue
    seenSkills.add(name)
    rows.push({
      key: `skill-${name}`,
      kind: 'skill',
      name,
      label: skillLabel(name),
      detail: name,
      state: 'loaded',
    })
  }

  const latestTools = new Map()
  trace.forEach((item, index) => {
    if (item.type !== 'tool') return
    const name = String(item.name || '').trim()
    if (!name) return
    const failed = item.success === false
    latestTools.set(name, {
      order: index,
      row: {
        key: `tool-${name}`,
        kind: 'tool',
        name,
        label: toolLabel(name),
        detail: name,
        state: failed ? 'failed' : 'done',
        success: !failed,
      },
    })
  })
  rows.push(
    ...[...latestTools.values()]
      .sort((left, right) => left.order - right.order)
      .map((entry) => entry.row),
  )

  return rows
}

export function nextPlannedTool(payload) {
  const planned = agentPlanTools(latestAgentPlan(payload))
  const done = agentTraceItems(payload).filter((item) => item.kind === 'tool')
  const last = done.at(-1)
  if (last && last.success === false) return String(last.name || '').trim()
  if (done.length >= planned.length) return ''
  return planned[done.length] || ''
}

export function currentAgentActionText(payload) {
  const status = payload?.status
  if (status === 'awaiting_script_choice') return '等待选择创意脚本'
  if (status === 'awaiting_video_choice') return '等待选择候选视频'
  if (status === 'failed') {
    const failed = agentTraceItems(payload).find((item) => item.state === 'failed')
    return failed ? `${failed.label}失败` : ''
  }
  if (status !== 'running') return ''
  const next = nextPlannedTool(payload)
  return next ? `正在执行${toolLabel(next)}` : '正在按编排计划执行'
}

export function agentTraceSummary(payload) {
  const items = agentTraceItems(payload)
  const skills = items.filter((item) => item.kind === 'skill')
  const tools = items.filter((item) => item.kind === 'tool')
  const failed = tools.find((item) => item.state === 'failed')
  if (failed) return `${failed.label}失败`
  const parts = []
  if (latestAgentPlan(payload)) parts.push('已制定编排计划')
  if (skills.length) parts.push(`已加载 ${skills.length} 个 Skill`)
  if (tools.length) parts.push(`已执行 ${tools.length} 个工具`)
  return parts.join(' · ')
}

function stringList(value) {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter(Boolean)
}
