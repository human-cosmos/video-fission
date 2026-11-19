import { describe, expect, it } from 'vitest'
import {
  agentMessagePhase,
  agentMessageStatusText,
  canShowTraceOutput,
  compactTitle,
  conversationStateLabel,
  groupExecutionItems,
  hasVideoPlanContent,
  isAgentCommentary,
  isConversationListEvent,
  isProductionRefreshEvent,
  isRefreshEvent,
  itemLabel,
  mergeConversationSummaries,
  payloadText,
  toolCallName,
} from './events'

describe('conversation event helpers', () => {
  it('formats compact titles', () => {
    expect(compactTitle('  一个很长   的标题  ')).toBe('一个很长 的标题')
    expect(compactTitle('x'.repeat(50))).toHaveLength(39)
  })

  it('extracts runtime item text in priority order', () => {
    expect(payloadText({ payload: { text: 'answer', output: 'log' } })).toBe('answer')
    expect(payloadText({ payload: { output: 'log' } })).toBe('log')
  })

  it('labels tools and localizes item types', () => {
    expect(itemLabel('commandExecution')).toBe('命令')
    expect(itemLabel('video_output')).toBe('成片')
    expect(itemLabel('video_observe')).toBe('观察')
    expect(toolCallName({ payload: { tool: 'create_video_brief' } })).toBe(
      'create_video_brief',
    )
  })

  it('distinguishes agent progress updates from final answers', () => {
    const progress = {
      item_type: 'agentMessage',
      status: 'completed',
      payload: { phase: 'commentary', text: '正在处理' },
    }
    const final = {
      item_type: 'agentMessage',
      status: 'completed',
      payload: { phase: 'final_answer', text: '处理完成' },
    }

    expect(agentMessagePhase(progress)).toBe('commentary')
    expect(isAgentCommentary(progress)).toBe(true)
    expect(agentMessageStatusText(progress)).toBe('进度更新')
    expect(isAgentCommentary(final)).toBe(false)
    expect(agentMessageStatusText(final)).toBe('已完成')
    expect(agentMessageStatusText({ ...progress, status: 'inProgress' })).toBe('执行中')
  })

  it('keeps command output out of the visible trace', () => {
    expect(canShowTraceOutput({ item_type: 'commandExecution' })).toBe(false)
    expect(canShowTraceOutput({ item_type: 'functionCallOutput' })).toBe(false)
    expect(canShowTraceOutput({ item_type: 'reasoning' })).toBe(true)
  })

  it('selects refresh events without waking on heartbeats', () => {
    expect(isRefreshEvent('agent.message.delta')).toBe(true)
    expect(isRefreshEvent('approval.requested')).toBe(true)
    expect(isRefreshEvent('item_upserted')).toBe(true)
    expect(isRefreshEvent('production_updated')).toBe(true)
    expect(isRefreshEvent('thread.name.updated')).toBe(true)
    expect(isRefreshEvent('stream.heartbeat')).toBe(false)
  })

  it('refreshes the list on state changes but not token deltas', () => {
    expect(isConversationListEvent('reasoning.text.delta')).toBe(false)
    expect(isConversationListEvent('agent.message.delta')).toBe(false)
    expect(isConversationListEvent('item.completed')).toBe(true)
    expect(isConversationListEvent('turn.completed')).toBe(true)
    expect(isConversationListEvent('thread.name.updated')).toBe(true)
    expect(isProductionRefreshEvent('production_updated')).toBe(true)
    expect(isProductionRefreshEvent('item.completed')).toBe(false)
  })

  it('labels aggregated conversation states', () => {
    expect(conversationStateLabel({ list_state: 'running' })).toBe('运行中')
    expect(conversationStateLabel({ list_state: 'completed' })).toBe('已完成')
    expect(conversationStateLabel({ list_state: 'warning' })).toBe('工具警告')
    expect(conversationStateLabel({ list_state: 'failed' })).toBe('执行失败')
    expect(conversationStateLabel({ list_state: 'empty' })).toBe('草稿')
  })

  it('merges a background status update without dropping loaded pages', () => {
    const merged = mergeConversationSummaries(
      [
        { id: 'a', list_state: 'running', title: 'A' },
        { id: 'b', list_state: 'completed', title: 'B' },
      ],
      [{ id: 'a', list_state: 'completed', title: 'A' }],
    )
    expect(merged).toEqual([
      { id: 'a', list_state: 'completed', title: 'A' },
      { id: 'b', list_state: 'completed', title: 'B' },
    ])
  })

  it('groups adjacent execution events while preserving messages', () => {
    const rows = groupExecutionItems(
      [
        { id: 'r1', item_type: 'reasoning', status: 'completed' },
        { id: 'c1', item_type: 'commandExecution', status: 'completed' },
        { id: 'a1', item_type: 'agentMessage', status: 'completed' },
        { id: 't1', item_type: 'dynamicToolCall', status: 'inProgress' },
      ],
      { includeTrace: true },
    )

    expect(rows).toHaveLength(3)
    expect(rows[0]).toMatchObject({
      item_type: 'executionGroup',
      status: 'completed',
      items: [{ id: 'r1' }, { id: 'c1' }],
    })
    expect(rows[1].id).toBe('a1')
    expect(rows[2]).toMatchObject({ id: 't1', status: 'inProgress' })
  })

  it('hides an empty duplicate plan card until real planning content exists', () => {
    const emptyPlan = {
      id: 'p1',
      item_type: 'video_plan',
      payload: { agent_trace: [{ type: 'plan', steps: ['ingest_reference_video'] }] },
    }
    expect(hasVideoPlanContent(emptyPlan)).toBe(false)
    expect(groupExecutionItems([emptyPlan])).toEqual([])

    const planWithOpening = {
      ...emptyPlan,
      payload: { ...emptyPlan.payload, opening: { start_ms: 0, end_ms: 2700 } },
    }
    expect(hasVideoPlanContent(planWithOpening)).toBe(true)
    expect(groupExecutionItems([planWithOpening])).toHaveLength(1)
  })

  it('marks grouped execution as failed when a step failed', () => {
    const [group] = groupExecutionItems(
      [
        { id: 'r1', item_type: 'reasoning', status: 'completed' },
        { id: 'c1', item_type: 'commandExecution', status: 'failed' },
      ],
      { includeTrace: true },
    )

    expect(group.status).toBe('failed')
  })

  it('removes reasoning and execution traces from the visible conversation', () => {
    const rows = groupExecutionItems([
      { id: 'u1', item_type: 'userMessage', status: 'completed' },
      { id: 'r1', item_type: 'reasoning', status: 'completed' },
      { id: 'c1', item_type: 'commandExecution', status: 'completed' },
      { id: 'f1', item_type: 'functionCallOutput', status: 'completed' },
      { id: 't1', item_type: 'dynamicToolCall', status: 'completed' },
      { id: 'a1', item_type: 'agentMessage', status: 'completed' },
    ])

    expect(rows.map((item) => item.id)).toEqual(['u1', 'a1'])
  })
})

