import { describe, expect, it } from 'vitest'
import {
  claimText,
  hasNonEmptyObserve,
  absoluteMediaMs,
  isLocalOpeningEstimate,
  isModelOpening,
  isOpeningShot,
  mediaDurationMs,
  mediaTimeMs,
  observeHighlights,
  openingOf,
  openingRangeText,
  openingTitle,
  openingTrack,
  segmentInOpening,
  shouldShowObserveCard,
  shouldShowOpening,
  slotTrail,
  videoDescribe,
  visibleObserveSegments,
  visibleVideoTags,
} from './understandDisplay'

const fixture = {
  production_id: 'prd_demo',
  status: 'completed',
  updated_at: '2026-11-18T09:50:00+08:00',
  summary: '一支遮瑕膏开箱短视频',
  description: '开场用面部特写抓住注意，中段展示膏体和妆效，结尾促单。',
  video_tags: {
    product_name: ['三色遮瑕膏'],
    video_describe: '应展示在 description 而不是标签里',
    video_summary: '摘要已在卡片顶部',
    environment_categories: ['室内'],
    style_categories: ['美妆口播'],
    narration_method_categories: ['第一人称口播'],
    BGM_categories: [],
    opening_types: '',
  },
  segments: [
    {
      id: 'seg_01',
      role: 'hook',
      start_ms: 0,
      end_ms: 2133,
      summary: '面部特写开场',
      description: '展示痘印和膏盒',
      slot: ['开场钩子'],
      sub_slot: ['结果钩子'],
      sub_slot_desc: ['结果状态特写'],
      reuse_recommended: false,
      clip_url: '/api/productions/prd_demo/artifacts/clips/seg_01.mp4',
      observe: {
        shot_observation_card: [
          {
            shot_id: 1,
            visual_subjects: '年轻女性面部特写与遮瑕膏盒',
            visual_actions: '指腹轻按脸颊',
            speech_transcript: '청담동 여배우들이',
            sequence_function_observation: '用瑕疵特写和背书开场',
          },
        ],
        raw_claims: [
          {
            raw_claim_id: 'RC1',
            raw_claim: '청담동 여배우들이 며칠밤을 꼬박 세워',
            start_time: 0,
            end_time: 2.133,
          },
        ],
        speech_evidence: {
          language: 'ko',
          segments: [{ start: 0, end: 2.133, text: '청담동 여배우들이 며칠밤을 꼬박 세워' }],
        },
      },
      error: null,
    },
    {
      id: 'seg_02',
      role: 'value',
      start_ms: 2133,
      end_ms: 5400,
      summary: '旧字段兼容段',
      observe: { shot_observation_card: [], raw_claims: [], speech_evidence: {} },
    },
    {
      id: 'seg_03',
      role: 'cta',
      start_ms: 5400,
      end_ms: 8100,
      error: '深度观察失败：网关超时',
      observe: {},
    },
  ],
}

const legacyPayload = {
  summary: '本地回退摘要',
  segments: [
    { id: 'seg_01', role: 'hook', start_ms: 0, end_ms: 2700, summary: '开场' },
    { id: 'seg_02', role: 'value', start_ms: 2700, end_ms: 5400, summary: '卖点' },
  ],
}

describe('understand display helpers', () => {
  it('shows description and only non-empty key tags', () => {
    expect(videoDescribe(fixture)).toBe(fixture.description)
    expect(visibleVideoTags(fixture.video_tags).map((item) => item.key)).toEqual([
      'product_name',
      'environment_categories',
      'style_categories',
      'narration_method_categories',
    ])
    expect(visibleVideoTags(fixture.video_tags).some((item) => item.key === 'BGM_categories')).toBe(
      false,
    )
  })

  it('joins slot / sub_slot / sub_slot_desc and keeps legacy segments empty', () => {
    expect(slotTrail(fixture.segments[0])).toBe('开场钩子 · 结果钩子 · 结果状态特写')
    expect(slotTrail(legacyPayload.segments[0])).toBe('')
  })

  it('hides a blank observe card and keeps error or non-empty segments', () => {
    expect(hasNonEmptyObserve(fixture.segments[1].observe)).toBe(false)
    expect(hasNonEmptyObserve({})).toBe(false)
    expect(shouldShowObserveCard(legacyPayload)).toBe(false)
    expect(shouldShowObserveCard({ segments: [] })).toBe(false)
    const visible = visibleObserveSegments(fixture)
    expect(visible.map((item) => item.id)).toEqual(['seg_01', 'seg_03'])
    expect(shouldShowObserveCard(fixture)).toBe(true)
  })

  it('picks observation highlights and claim text', () => {
    const highlights = observeHighlights(fixture.segments[0].observe.shot_observation_card[0])
    expect(highlights.map((item) => item.label)).toEqual(['作用', '主体', '动作', '口播'])
    expect(claimText(fixture.segments[0].observe.raw_claims[0])).toContain('여배우들')
  })

  it('treats small fractional values as seconds and large integers as milliseconds', () => {
    expect(mediaTimeMs(2.133)).toBe(2133)
    expect(mediaTimeMs(0)).toBe(0)
    expect(mediaTimeMs(2133)).toBe(2133)
  })

  it('formats back-end normalized original timeline values without adding offsets twice', () => {
    const segment = { start_ms: 2133, end_ms: 5400 }
    expect(absoluteMediaMs(segment, 0)).toBe(0)
    expect(absoluteMediaMs(segment, 2.133)).toBe(2133)
    expect(absoluteMediaMs(segment, 1500)).toBe(1500)
    expect(absoluteMediaMs(segment, 4266)).toBe(4266)
  })

  it('reads description from video_tags when top-level description is absent', () => {
    expect(videoDescribe({ video_tags: { video_describe: '标签里的描述' } })).toBe('标签里的描述')
    expect(videoDescribe(legacyPayload)).toBe('')
  })
})

describe('opening span display', () => {
  const modelOpening = {
    start_ms: 0,
    end_ms: 12400,
    reason: '口播说完处境后镜头切到柜台，节奏从开场转入卖点。',
    method: 'model',
    segment_ids: ['seg_01', 'seg_02'],
    shot_id: 'shot_opening',
  }
  const localOpening = {
    start_ms: 0,
    end_ms: 8100,
    reason: '短片全程同一开场节奏',
    method: 'local_fallback',
  }

  it('formats a model opening range and keeps the reason', () => {
    expect(openingRangeText(modelOpening)).toBe('0:00.000–0:12.400')
    expect(openingTitle(modelOpening)).toBe('开头 0:00.000–0:12.400')
    expect(isModelOpening(modelOpening)).toBe(true)
    expect(isLocalOpeningEstimate(modelOpening)).toBe(false)
  })

  it('labels local estimates as provisional, not as a confirmed model opening', () => {
    expect(shouldShowOpening(localOpening)).toBe(true)
    expect(isModelOpening(localOpening)).toBe(false)
    expect(isLocalOpeningEstimate(localOpening)).toBe(true)
    expect(openingTitle(localOpening)).toBe('本地暂估开头 0:00.000–0:08.100')
    expect(isModelOpening({ end_ms: 12400 })).toBe(false)
    expect(isLocalOpeningEstimate({ end_ms: 12400 })).toBe(false)
    expect(openingTitle({ end_ms: 12400 })).toBe('开头区间 0:00.000–0:12.400')
  })

  it('hides missing or invalid openings instead of inventing a 10–15s span', () => {
    expect(shouldShowOpening(null)).toBe(false)
    expect(shouldShowOpening({})).toBe(false)
    expect(shouldShowOpening({ method: 'model', end_ms: 0 })).toBe(false)
    expect(openingTitle({ method: 'model' })).toBe('')
    expect(openingOf({ summary: 'no opening' })).toBe(null)
    expect(openingOf({ decomposition: { opening: modelOpening } })).toEqual(modelOpening)
    expect(openingOf({ opening: modelOpening })).toEqual(modelOpening)
    expect(openingOf({ script: { opening: modelOpening } })).toBe(null)
  })

  it('builds timeline highlight data from [0, opening_end) against the source duration', () => {
    const payload = {
      opening: modelOpening,
      source_video: { duration_ms: 40000 },
      segments: [
        { id: 'seg_01', start_ms: 0, end_ms: 2400 },
        { id: 'seg_02', start_ms: 2400, end_ms: 12400 },
        { id: 'seg_03', start_ms: 12400, end_ms: 40000 },
      ],
    }
    expect(mediaDurationMs(payload)).toBe(40000)
    expect(openingTrack(modelOpening, 40000)).toEqual({
      startPct: 0,
      widthPct: 31,
      endMs: 12400,
      durationMs: 40000,
    })
    expect(segmentInOpening(payload.segments[0], modelOpening)).toBe(true)
    expect(segmentInOpening(payload.segments[1], modelOpening)).toBe(true)
    expect(segmentInOpening(payload.segments[2], modelOpening)).toBe(false)
    expect(isOpeningShot({ shot_id: 'shot_opening', start_ms: 0, duration_ms: 12400 }, modelOpening)).toBe(true)
    expect(isOpeningShot({ id: 'shot_02', start_ms: 12400, duration_ms: 8000 }, modelOpening)).toBe(false)
    expect(isOpeningShot({ shot_id: 'shot_opening', source_kind: 'opening' }, null)).toBe(true)
    expect(isOpeningShot({ id: 'shot_03', start_ms: 12400, duration_ms: 8000 }, null)).toBe(false)
  })
})
