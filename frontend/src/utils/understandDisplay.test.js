import { describe, expect, it } from 'vitest'
import {
  claimText,
  hasNonEmptyObserve,
  absoluteMediaMs,
  mediaTimeMs,
  observeHighlights,
  shouldShowObserveCard,
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
      error: '片段观察失败：网关超时',
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
