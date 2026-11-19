<script setup>
import { ChevronRight } from '@lucide/vue'
import { computed } from 'vue'
import { ROLE_LABELS, formatTimecode } from '../utils/format'
import {
  claimText,
  hasNonEmptyObserve,
  absoluteMediaMs,
  observeHighlights,
  shouldShowObserveCard,
  visibleObserveSegments,
} from '../utils/understandDisplay'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
})

const visible = computed(() => shouldShowObserveCard(props.payload))
const rows = computed(() => visibleObserveSegments(props.payload))

function cardsOf(segment) {
  const cards = segment?.observe?.shot_observation_card
  return Array.isArray(cards) ? cards : []
}

function claimsOf(segment) {
  const claims = segment?.observe?.raw_claims
  if (!Array.isArray(claims)) return []
  return claims.map((claim) => ({
    id: claim?.raw_claim_id || claimText(claim),
    text: claimText(claim),
    startMs: absoluteMediaMs(segment, claim?.start_ms ?? claim?.start_time ?? claim?.start),
    endMs: absoluteMediaMs(segment, claim?.end_ms ?? claim?.end_time ?? claim?.end),
  })).filter((item) => item.text)
}

function speechOf(segment) {
  const evidence = segment?.observe?.speech_evidence
  if (!evidence || typeof evidence !== 'object') return { notes: '', segments: [] }
  const parts = Array.isArray(evidence.segments) ? evidence.segments : []
  return {
    notes: String(evidence.notes || '').trim(),
    language: evidence.language || '',
    segments: parts
      .map((item) => ({
        startMs: absoluteMediaMs(segment, item?.start_ms ?? item?.start),
        endMs: absoluteMediaMs(segment, item?.end_ms ?? item?.end),
        text: String(item?.text || '').trim(),
      }))
      .filter((item) => item.text),
  }
}
</script>

<template>
  <section v-if="visible" class="video-card studio-card" data-testid="video-observe-card">
    <details class="studio-disclosure" data-testid="video-observe-disclosure">
      <summary class="studio-disclosure-summary">
        <span>
          <strong>片段观察</strong>
          <small>{{ rows.length }} 段视听事实</small>
        </span>
        <ChevronRight :size="15" />
      </summary>
      <div class="studio-disclosure-body">
    <article
      v-for="segment in rows"
      :key="segment.id"
      class="observe-block"
      :data-testid="`video-observe-${segment.id}`"
    >
      <header>
        <strong>{{ ROLE_LABELS[segment.role] || segment.role || segment.id }}</strong>
        <span class="segment-time">
          {{ formatTimecode(segment.start_ms) }} – {{ formatTimecode(segment.end_ms) }}
        </span>
      </header>
      <p v-if="segment.error" class="page-error" :data-testid="`video-observe-error-${segment.id}`">
        {{ segment.error }}
      </p>
      <template v-else-if="hasNonEmptyObserve(segment.observe)">
        <dl v-for="(card, index) in cardsOf(segment)" :key="`${segment.id}-card-${index}`" class="observe-highlights">
          <div v-for="item in observeHighlights(card)" :key="item.key">
            <dt>{{ item.label }}</dt>
            <dd>{{ item.text }}</dd>
          </div>
        </dl>
        <ul v-if="claimsOf(segment).length" class="observe-claims" :data-testid="`video-observe-claims-${segment.id}`">
          <li v-for="claim in claimsOf(segment)" :key="claim.id">
            <span v-if="claim.endMs" class="segment-time">
              {{ formatTimecode(claim.startMs) }} – {{ formatTimecode(claim.endMs) }}
            </span>
            {{ claim.text }}
          </li>
        </ul>
        <div
          v-if="speechOf(segment).notes || speechOf(segment).segments.length"
          class="observe-speech"
          :data-testid="`video-observe-speech-${segment.id}`"
        >
          <p v-if="speechOf(segment).notes" class="page-muted">{{ speechOf(segment).notes }}</p>
          <ol v-if="speechOf(segment).segments.length">
            <li v-for="(item, index) in speechOf(segment).segments" :key="`${segment.id}-speech-${index}`">
              <span class="segment-time">
                {{ formatTimecode(item.startMs) }} – {{ formatTimecode(item.endMs) }}
              </span>
              {{ item.text }}
            </li>
          </ol>
        </div>
      </template>
    </article>
      </div>
    </details>
  </section>
</template>
