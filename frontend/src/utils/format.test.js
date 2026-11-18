import { describe, expect, it } from 'vitest'
import {
  formatBytes,
  formatDuration,
  formatTimecode,
  historyStatusText,
  humanizeMsText,
  isTechnicalVisual,
  needsRemoteFields,
  pickPositiveBytes,
  referenceLabel,
  segmentFrameNames,
} from './format'

describe('format helpers', () => {
  it('formats timecodes and durations in milliseconds', () => {
    expect(formatTimecode(0)).toBe('0:00.000')
    expect(formatTimecode(1500)).toBe('0:01.500')
    expect(formatTimecode(61_250)).toBe('1:01.250')
    expect(formatDuration(800)).toBe('800 ms')
    expect(formatDuration(3000)).toBe('3 秒')
    expect(formatDuration(1500)).toBe('1.5 秒')
  })

  it('formats byte sizes', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(1_572_864)).toBe('1.5 MB')
  })

  it('uses conventional keyframe artifact names without inventing contract fields', () => {
    expect(segmentFrameNames('seg_01')).toEqual([
      'seg_01_frame_01.jpg',
      'seg_01_frame_02.jpg',
      'seg_01_frame_03.jpg',
    ])
  })

  it('shows a basename for reference paths', () => {
    expect(referenceLabel('skills/video-references/references/食品零食/84_促单_8S_无口播.md')).toBe(
      '84_促单_8S_无口播.md',
    )
    expect(referenceLabel('plain')).toBe('plain')
  })

  it('drops a redundant completed stage in history status', () => {
    expect(historyStatusText({ status: 'completed', current_stage: 'done' })).toBe('已完成')
    expect(historyStatusText({ status: 'running', current_stage: 'generate' })).toBe('进行中 · 生成')
  })

  it('picks a real byte size and ignores zeros', () => {
    expect(pickPositiveBytes(0, undefined, '1024', 0)).toBe(1024)
    expect(pickPositiveBytes(0, null)).toBe(0)
  })

  it('humanizes millisecond dumps and detects probe visuals', () => {
    expect(humanizeMsText('HOOK 段，时长 2700ms。')).toBe('HOOK 段，时长 2.7 秒。')
    expect(isTechnicalVisual('1080x1920 @30.00fps，0-6000ms，抽取 3 帧')).toBe(true)
    expect(isTechnicalVisual('开箱特写，光线干净')).toBe(false)
  })

  it('only asks for remote credential fields on remote providers', () => {
    expect(needsRemoteFields('local_ffmpeg')).toBe(false)
    expect(needsRemoteFields('none')).toBe(false)
    expect(needsRemoteFields('openai_compatible')).toBe(true)
    expect(needsRemoteFields('generic_http')).toBe(true)
  })
})
