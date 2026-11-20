import { describe, expect, it } from 'vitest'
import {
  VIDEO_ITEM_TYPES,
  WORKSPACE_MODES,
  aspectClass,
  cleanDisplayText,
  formatBytes,
  formatDuration,
  formatMessageTime,
  formatTimecode,
  historyStatusText,
  humanizeMsText,
  isImageFile,
  isTechnicalVisual,
  isVideoFile,
  needsRemoteFields,
  pickPositiveBytes,
  referenceLabel,
  segmentAction,
  segmentFrameNames,
  toProductionVideoFile,
  withRevision,
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

  it('formats message timestamps for the local conversation view', () => {
    const now = new Date(2026, 10, 19, 15, 30)
    expect(formatMessageTime(new Date(2026, 10, 19, 14, 5), now)).toBe('14:05')
    expect(formatMessageTime(new Date(2026, 10, 18, 9, 8), now)).toBe('11-18 09:08')
    expect(formatMessageTime(new Date(2025, 10, 18, 9, 8), now)).toBe('2025-11-18 09:08')
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
    expect(historyStatusText({ status: 'awaiting_creative_context', current_stage: 'intent' })).toBe(
      '等待生成脚本',
    )
    expect(historyStatusText({ status: 'awaiting_script_approval', current_stage: 'script' })).toBe(
      '待确认脚本',
    )
    expect(historyStatusText({ status: 'awaiting_script_choice', current_stage: 'script' })).toBe(
      '自动生成准备中',
    )
    expect(historyStatusText({ status: 'awaiting_video_choice', current_stage: 'compose' })).toBe(
      '待选择视频',
    )
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
    expect(needsRemoteFields('gemini_gateway')).toBe(true)
    expect(needsRemoteFields('seedance')).toBe(true)
  })
})

describe('chat-native production helpers', () => {
  it('keeps chat, settings, and help workspace modes', () => {
    expect(WORKSPACE_MODES.map((item) => item.id)).toEqual(['chat', 'settings', 'help'])
    expect(WORKSPACE_MODES.some((item) => item.id === 'studio')).toBe(false)
    expect(VIDEO_ITEM_TYPES).toContain('video_observe')
  })

  it('detects production and filename videos', () => {
    expect(isVideoFile({ kind: 'production_video', name: 'ref.bin' })).toBe(true)
    expect(isVideoFile({ name: 'hook.mp4' })).toBe(true)
    expect(isVideoFile({ content_type: 'video/webm', name: 'clip' })).toBe(true)
    expect(isVideoFile({ name: 'notes.md' })).toBe(false)
  })

  it('detects uploaded reference images', () => {
    expect(isImageFile({ kind: 'reference_image', name: 'product.bin' })).toBe(true)
    expect(isImageFile({ content_type: 'image/png', name: 'reference' })).toBe(true)
    expect(isImageFile({ name: 'reference.webp' })).toBe(true)
    expect(isImageFile({ name: 'reference.mp4' })).toBe(false)
  })

  it('classifies player aspect and revises artifact urls', () => {
    expect(aspectClass({ width: 1280, height: 720 })).toBe('player-frame is-landscape')
    expect(aspectClass({ width: 1080, height: 1920 })).toBe('player-frame is-portrait')
    expect(withRevision('/api/productions/prd_1/artifacts/final.mp4', '2026-11-18')).toBe(
      '/api/productions/prd_1/artifacts/final.mp4?t=2026-11-18',
    )
  })

  it('resolves segment actions from replace_plan first', () => {
    expect(
      segmentAction({ id: 'seg_02' }, { resolved_segment_ids: ['seg_02', 'seg_03'] }),
    ).toBe('generate')
    expect(segmentAction({ id: 'seg_01' }, { resolved_segment_ids: ['seg_02'] })).toBe('reuse')
    expect(
      segmentAction({ id: 'seg_02' }, null, [{ source_segment_id: 'seg_02', action: 'generate' }]),
    ).toBe('generate')
  })

  it('cleans technical crumbs and leaves creative copy', () => {
    expect(cleanDisplayText('时长 2700ms。场景切点划分。展示核心卖点')).toBe('展示核心卖点')
    expect(cleanDisplayText('1080x1920 @30.00fps，抽取 3 帧')).toBe('')
    expect(cleanDisplayText('开场抓住注意')).toBe('开场抓住注意')
  })

  it('serializes a production video attachment without a filesystem path', () => {
    expect(
      toProductionVideoFile({
        id: 'upl_1',
        name: 'sample-reference.mp4',
        path: '/secret/uploads/productions/upl_1.mp4',
        size: 12,
        width: 1280,
        height: 720,
        duration_ms: 8100,
        content_type: 'video/mp4',
      }),
    ).toEqual({
      kind: 'production_video',
      id: 'upl_1',
      name: 'sample-reference.mp4',
      path: '',
      size: 12,
      content_type: 'video/mp4',
      duration_ms: 8100,
      width: 1280,
      height: 720,
    })
  })
})
