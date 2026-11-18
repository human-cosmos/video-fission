import { describe, expect, it } from 'vitest'
import {
  isAwaitingScriptChoice,
  isSelectedCandidate,
  landingClaimsComplete,
  landingPageOf,
  landingParseStatus,
  landingStatusText,
  scriptCandidatesOf,
  selectedScriptIdOf,
  sharedPlanShots,
  shouldOfferRegenerateScripts,
  shouldOfferRegenerateVideo,
  shouldOfferScriptChoice,
  shouldShowLanding,
  voiceoverSummary,
} from './planDisplay'

const shots = [
  { id: 'shot_opening', start_ms: 0, end_ms: 8100, duration_ms: 8100, action: 'reuse', voiceover: '先看斑点再上膏体' },
]

const candidates = [
  { id: 'script_a', title: '痛点切入', angle: '先讲使用困扰再给方案', script: { shots, opening: { shot_id: 'shot_opening' } } },
  { id: 'script_b', title: '效果对比', angle: '前后对比突出效果', script: { shots: [{ ...shots[0], voiceover: '两小时后斑点淡了' }] } },
  { id: 'script_c', title: '人群承诺', angle: '对忙碌职场人说省时', script: { shots: [{ ...shots[0], voiceover: '出门前一分钟就够' }] } },
]

describe('plan card landing and script candidates', () => {
  it('summarizes a successful landing page without extra complete-analysis copy', () => {
    const landing = {
      source_url: 'https://example.com/p',
      parse_status: 'ok',
      method: 'local_extract',
      product: '遮瑕膏',
      selling_points: ['轻薄', '不卡纹'],
      benefits: ['遮斑'],
      audience: '通勤妆',
      promises: ['自然妆效'],
    }
    expect(landingPageOf({ landing_page: landing })).toEqual(landing)
    expect(shouldShowLanding(landing)).toBe(true)
    expect(landingParseStatus(landing)).toBe('ok')
    expect(landingClaimsComplete(landing)).toBe(true)
    expect(landingStatusText(landing)).toBe('落地页卖点')
    expect(landingStatusText(landing)).not.toMatch(/已完整/)
  })

  it('does not pretend a failed or partial landing page was fully parsed', () => {
    const failed = { source_url: 'https://example.com/p', parse_status: 'failed', method: 'local_extract' }
    expect(landingStatusText(failed)).toBe('落地页抓取失败，可在对话里粘贴页面正文后再解析')
    expect(landingClaimsComplete(failed)).toBe(false)
    const partial = { source_url: 'https://example.com/p', parse_status: 'partial', selling_points: ['标题党'] }
    expect(landingStatusText(partial)).toBe('落地页仅部分解析，未当作完整卖点')
    expect(landingClaimsComplete(partial)).toBe(false)
    expect(shouldShowLanding(failed)).toBe(true)
  })

  it('reads three script candidates and their voiceover summaries', () => {
    const payload = { script_candidates: candidates, selected_script_id: null }
    expect(scriptCandidatesOf(payload).map((item) => item.id)).toEqual(['script_a', 'script_b', 'script_c'])
    expect(voiceoverSummary(candidates[0])).toBe('先看斑点再上膏体')
    expect(voiceoverSummary(candidates[1])).toBe('两小时后斑点淡了')
    expect(voiceoverSummary(candidates[2])).toBe('出门前一分钟就够')
    expect(new Set(candidates.map(voiceoverSummary)).size).toBe(3)
    expect(sharedPlanShots(payload)[0].id).toBe('shot_opening')
  })

  it('offers choose only while awaiting a selection', () => {
    const awaiting = {
      production_id: 'prd_1',
      status: 'awaiting_script_choice',
      script_candidates: candidates,
      selected_script_id: null,
    }
    expect(isAwaitingScriptChoice(awaiting)).toBe(true)
    expect(shouldOfferScriptChoice(awaiting)).toBe(true)
    expect(shouldOfferRegenerateScripts(awaiting)).toBe(true)
    expect(shouldOfferRegenerateVideo(awaiting)).toBe(false)
    expect(isSelectedCandidate(candidates[1], awaiting)).toBe(false)
  })

  it('marks the selected script and only then offers regenerate-video', () => {
    const selected = {
      production_id: 'prd_1',
      status: 'completed',
      script_candidates: candidates,
      selected_script_id: 'script_b',
      script: candidates[1].script,
    }
    expect(shouldOfferScriptChoice(selected)).toBe(false)
    expect(shouldOfferRegenerateScripts(selected)).toBe(true)
    expect(shouldOfferRegenerateVideo(selected)).toBe(true)
    expect(isSelectedCandidate(candidates[1], selected)).toBe(true)
    expect(isSelectedCandidate(candidates[0], selected)).toBe(false)
  })

  it('does not let regenerate-video fire while running or still waiting to choose', () => {
    const running = {
      status: 'running',
      script_candidates: candidates,
      selected_script_id: 'script_a',
    }
    expect(shouldOfferRegenerateVideo(running)).toBe(false)
    expect(shouldOfferRegenerateScripts(running)).toBe(false)
    expect(shouldOfferScriptChoice({ status: 'running', script_candidates: candidates })).toBe(false)
  })
})
