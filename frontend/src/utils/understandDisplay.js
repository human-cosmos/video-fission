export const VIDEO_TAG_LABELS = {
  product_name: '商品',
  environment_categories: '场景类型',
  environment_specific_scenes: '具体场景',
  model_types: '模特类型',
  model_social_role: '模特角色',
  model_appearance_features: '模特外形',
  style_categories: '风格',
  style_details: '风格细节',
  product_presentation_types: '产品呈现',
  product_showcase_forms: '展示形式',
  narration_method_categories: '口播方式',
  narration_method_details: '口播细节',
  opening_types: '开场类型',
  opening_forms: '开场形式',
  narrative_structures: '叙事结构',
  rhythm_patterns: '节奏',
  interaction_modes: '互动方式',
  BGM_categories: 'BGM 类型',
  BGM_styles: 'BGM 风格',
}

export const OBSERVE_HIGHLIGHT_FIELDS = [
  ['sequence_function_observation', '作用'],
  ['visual_subjects', '主体'],
  ['visual_actions', '动作'],
  ['speech_transcript', '口播'],
  ['camera_observation', '机位'],
  ['information_presentation_observation', '信息呈现'],
]

export function listValues(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item ?? '').trim()).filter(Boolean)
  }
  if (value == null) return []
  const text = String(value).trim()
  return text ? [text] : []
}

export function visibleVideoTags(tags) {
  if (!tags || typeof tags !== 'object') return []
  return Object.entries(VIDEO_TAG_LABELS)
    .map(([key, label]) => ({ key, label, values: listValues(tags[key]) }))
    .filter((item) => item.values.length > 0)
}

export function videoDescribe(payload) {
  const tags = payload?.video_tags || payload?.decomposition?.video_tags || {}
  return String(
    payload?.description ||
      payload?.video_describe ||
      tags.video_describe ||
      payload?.decomposition?.description ||
      '',
  ).trim()
}

export function videoTagsOf(payload) {
  return payload?.video_tags || payload?.decomposition?.video_tags || {}
}

export function slotTrail(segment) {
  if (!segment) return ''
  return [listValues(segment.slot), listValues(segment.sub_slot), listValues(segment.sub_slot_desc)]
    .map((values) => values.join(' / '))
    .filter(Boolean)
    .join(' · ')
}

export function hasNonEmptyObserve(observe) {
  if (!observe || typeof observe !== 'object') return false
  if (Array.isArray(observe.shot_observation_card) && observe.shot_observation_card.length > 0) {
    return true
  }
  if (Array.isArray(observe.raw_claims) && observe.raw_claims.length > 0) return true
  const evidence = observe.speech_evidence
  if (evidence && typeof evidence === 'object') {
    if (String(evidence.notes || '').trim()) return true
    if (Array.isArray(evidence.segments) && evidence.segments.some((item) => String(item?.text || '').trim())) {
      return true
    }
  }
  return false
}

export function visibleObserveSegments(payload) {
  const segments = payload?.segments || payload?.decomposition?.segments || []
  return segments.filter((segment) => Boolean(segment?.error) || hasNonEmptyObserve(segment?.observe))
}

export function shouldShowObserveCard(payload) {
  return visibleObserveSegments(payload).length > 0
}

export function mediaTimeMs(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return 0
  if (Number.isInteger(n) && n >= 1000) return n
  return Math.round(n * 1000)
}

export function absoluteMediaMs(_segment, value) {
  // Pipeline normalization converts clip-local observe clocks to the original
  // production timeline before persisting them.
  return mediaTimeMs(value)
}

export function observeHighlights(card) {
  if (!card || typeof card !== 'object') return []
  return OBSERVE_HIGHLIGHT_FIELDS.map(([key, label]) => {
    const text = String(card[key] || '').trim()
    return text ? { key, label, text } : null
  }).filter(Boolean)
}

export function claimText(claim) {
  if (!claim) return ''
  if (typeof claim === 'string') return claim.trim()
  return String(claim.raw_claim || claim.text || claim.claim || '').trim()
}
