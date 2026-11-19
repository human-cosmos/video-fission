import { describe, expect, it } from 'vitest'
import {
  agentPlanSkills,
  agentPlanTools,
  agentTraceItems,
  agentTraceOf,
  agentTraceSummary,
  currentAgentActionText,
  hasAgentTrace,
  latestAgentPlan,
  nextPlannedTool,
  skillLabel,
  toolLabel,
} from './agentTraceDisplay'

const trace = [
  {
    type: 'plan',
    skills: ['production-orchestration', 'video-references'],
    steps: [
      'ingest_reference_video',
      'understand_reference_video',
      'lock_timeline_opening',
      'write_intent_and_scripts',
    ],
  },
  { type: 'skill', name: 'production-orchestration' },
  { type: 'skill', name: 'video-references' },
  { type: 'tool', name: 'ingest_reference_video', success: true },
  { type: 'tool', name: 'understand_reference_video', success: true },
]

describe('agent trace display', () => {
  it('reads plan skills and tool steps from agent_trace', () => {
    const payload = { agent_trace: trace }
    expect(hasAgentTrace(payload)).toBe(true)
    expect(agentTraceOf({ agent_trace: null })).toEqual([])
    const plan = latestAgentPlan(payload)
    expect(agentPlanSkills(plan)).toEqual(['production-orchestration', 'video-references'])
    expect(agentPlanTools(plan)).toEqual([
      'ingest_reference_video',
      'understand_reference_video',
      'lock_timeline_opening',
      'write_intent_and_scripts',
    ])
    expect(skillLabel('production-orchestration')).toBe('出片编排')
    expect(toolLabel('prepare_video_production')).toBe('准备视频创作')
    expect(toolLabel('lock_timeline_opening')).toBe('锁定开场连续块')
  })

  it('turns the trace into plan, skill, and tool rows', () => {
    const items = agentTraceItems({ agent_trace: trace })
    expect(items.map((item) => item.kind)).toEqual(['plan', 'skill', 'skill', 'tool', 'tool'])
    expect(items[0].detail).toContain('接入参考视频')
    expect(items[0].skills.map((item) => item.label)).toEqual(['出片编排', '参考脚本'])
    expect(items[3]).toMatchObject({ kind: 'tool', name: 'ingest_reference_video', state: 'done' })
  })

  it('summarizes progress and names the next planned tool', () => {
    const payload = { status: 'running', agent_trace: trace }
    expect(nextPlannedTool(payload)).toBe('lock_timeline_opening')
    expect(currentAgentActionText(payload)).toBe('正在执行锁定开场连续块')
    expect(agentTraceSummary(payload)).toBe('已制定编排计划 · 已加载 2 个 Skill · 已执行 2 个工具')
  })

  it('surfaces a failed tool and script-choice pause without success claims', () => {
    const failed = {
      status: 'failed',
      agent_trace: [...trace, { type: 'tool', name: 'generate_video_shots', success: false }],
    }
    expect(agentTraceItems(failed).at(-1).state).toBe('failed')
    expect(currentAgentActionText(failed)).toBe('生成镜头失败')
    expect(agentTraceSummary(failed)).toBe('生成镜头失败')
    expect(agentTraceSummary(failed)).not.toMatch(/已还原|已按参考对齐/)

    const paused = {
      status: 'awaiting_script_choice',
      agent_trace: [
        ...trace,
        { type: 'tool', name: 'lock_timeline_opening', success: true },
        { type: 'tool', name: 'write_intent_and_scripts', success: true },
      ],
    }
    expect(currentAgentActionText(paused)).toBe('等待选择创意脚本')
    expect(nextPlannedTool(paused)).toBe('')
  })

  it('ignores malformed entries and unknown wrappers', () => {
    expect(hasAgentTrace({})).toBe(false)
    expect(agentTraceItems({ agent_trace: [null, { type: 'mystery' }, { type: 'tool' }] })).toEqual([])
    expect(toolLabel('custom_tool')).toBe('custom_tool')
  })

  it('normalizes recovered plans and duplicate trace entries', () => {
    const recovered = {
      status: 'running',
      agent_trace: [
        ...trace,
        { type: 'plan', skills: trace[0].skills, steps: trace[0].steps, resume_from: 'recover' },
        { type: 'skill', name: 'production-orchestration' },
        { type: 'skill', name: 'video-references' },
        { type: 'tool', name: 'ingest_reference_video', success: true },
      ],
    }

    const items = agentTraceItems(recovered)
    expect(items.filter((item) => item.kind === 'plan')).toHaveLength(1)
    expect(items.filter((item) => item.kind === 'skill')).toHaveLength(2)
    expect(items.filter((item) => item.kind === 'tool')).toHaveLength(2)
    expect(agentTraceSummary(recovered)).toBe(
      '已制定编排计划 · 已加载 2 个 Skill · 已执行 2 个工具',
    )
    expect(nextPlannedTool(recovered)).toBe('lock_timeline_opening')
  })

  it('keeps the latest state for a repeated tool', () => {
    const items = agentTraceItems({
      agent_trace: [
        ...trace,
        { type: 'tool', name: 'understand_reference_video', success: false },
      ],
    })
    const understand = items.find((item) => item.name === 'understand_reference_video')
    expect(understand).toMatchObject({ state: 'failed', success: false })
    expect(currentAgentActionText({
      status: 'failed',
      agent_trace: [{ type: 'tool', name: 'understand_reference_video', success: false }],
    })).toBe('理解参考视频失败')
  })
})
