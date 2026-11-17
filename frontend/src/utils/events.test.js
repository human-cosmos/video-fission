import { describe, expect, it } from 'vitest'
import {
  compactTitle,
  isRefreshEvent,
  itemLabel,
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
    expect(toolCallName({ payload: { tool: 'create_video_brief' } })).toBe(
      'create_video_brief',
    )
  })

  it('selects refresh events without waking on heartbeats', () => {
    expect(isRefreshEvent('agent.message.delta')).toBe(true)
    expect(isRefreshEvent('approval.requested')).toBe(true)
    expect(isRefreshEvent('stream.heartbeat')).toBe(false)
  })
})

