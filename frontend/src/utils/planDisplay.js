export function landingPageOf(payload) {
  const landing = payload?.landing_page
  if (!landing || typeof landing !== 'object') return null
  return landing
}

export function landingParseStatus(landing) {
  const status = String(landing?.parse_status || '').trim()
  if (status === 'ok' || status === 'partial' || status === 'failed') return status
  return ''
}

export function shouldShowLanding(landing) {
  if (!landing || typeof landing !== 'object') return false
  const points = listOf(landing.selling_points)
  const benefits = listOf(landing.benefits)
  const promises = listOf(landing.promises)
  return Boolean(
    landing.source_url
      || landing.product
      || landing.audience
      || landing.raw_excerpt
      || landingParseStatus(landing)
      || points.length
      || benefits.length
      || promises.length,
  )
}

export function landingStatusText(landing) {
  const status = landingParseStatus(landing)
  if (status === 'failed') return '落地页抓取失败，可在对话里粘贴页面正文后再解析'
  if (status === 'partial') return '落地页仅部分解析，未当作完整卖点'
  if (status === 'ok') return '落地页卖点'
  return shouldShowLanding(landing) ? '落地页' : ''
}

export function landingClaimsComplete(landing) {
  return landingParseStatus(landing) === 'ok'
}

export function scriptCandidatesOf(payload) {
  const list = payload?.script_candidates
  if (!Array.isArray(list)) return []
  return list.filter((item) => item && typeof item === 'object' && (item.id || item.title || item.script))
}

export function selectedScriptIdOf(payload) {
  const value = String(payload?.selected_script_id || '').trim()
  return value || ''
}

export function isAwaitingScriptChoice(payload) {
  return payload?.status === 'awaiting_script_choice'
}

export function candidateScript(candidate) {
  if (!candidate || typeof candidate !== 'object') return null
  return candidate.script && typeof candidate.script === 'object' ? candidate.script : null
}

export function voiceoverSummary(candidate) {
  const script = candidateScript(candidate) || candidate
  const shots = Array.isArray(script?.shots) ? script.shots : []
  const lines = shots
    .map((shot) => String(shot?.voiceover || shot?.on_screen_text || '').trim())
    .filter(Boolean)
  return lines.join(' / ')
}

export function sharedPlanShots(payload) {
  const direct = payload?.script?.shots
  if (Array.isArray(direct) && direct.length) return direct
  const first = scriptCandidatesOf(payload)[0]
  const shots = candidateScript(first)?.shots
  return Array.isArray(shots) ? shots : []
}

export function shouldOfferScriptChoice(payload) {
  return isAwaitingScriptChoice(payload) && scriptCandidatesOf(payload).length > 0
}

export function shouldOfferRegenerateScripts(payload) {
  if (!scriptCandidatesOf(payload).length && !payload?.production_id) return false
  const status = payload?.status
  if (status === 'running') return false
  return isAwaitingScriptChoice(payload) || Boolean(selectedScriptIdOf(payload)) || status === 'completed'
}

export function shouldOfferRegenerateVideo(payload) {
  if (payload?.status === 'running' || isAwaitingScriptChoice(payload)) return false
  return Boolean(selectedScriptIdOf(payload))
}

export function isSelectedCandidate(candidate, payload) {
  const selected = selectedScriptIdOf(payload)
  const id = String(candidate?.id || '').trim()
  return Boolean(selected && id && selected === id)
}

function listOf(value) {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter(Boolean)
}

export { listOf as landingList }
