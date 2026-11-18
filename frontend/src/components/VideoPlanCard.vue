<script setup>
import { computed } from 'vue'
import { cleanDisplayText, formatDuration, referenceLabel } from '../utils/format'
import { isOpeningShot, openingOf } from '../utils/understandDisplay'
import VideoOpeningRange from './VideoOpeningRange.vue'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
})

const intent = computed(() => props.payload?.intent || {})
const skeleton = computed(() => props.payload?.skeleton || {})
const references = computed(() => intent.value?.references || props.payload?.references || [])
const replacePlan = computed(() => props.payload?.replace_plan || null)
const resolved = computed(() => replacePlan.value?.resolved_segment_ids || [])
const opening = computed(() => openingOf(props.payload))
const shots = computed(() => props.payload?.script?.shots || props.payload?.shots || [])

function shotDuration(shot) {
  const explicit = Number(shot?.duration_ms)
  if (Number.isFinite(explicit) && explicit > 0) return explicit
  const start = Math.max(0, Number(shot?.start_ms) || 0)
  const end = Number(shot?.end_ms)
  if (Number.isFinite(end) && end > start) return end - start
  return 0
}

</script>

<template>
  <section class="video-card studio-card" data-testid="video-plan-card">
    <h3>任务规划</h3>
    <VideoOpeningRange :payload="payload" />
    <ol v-if="shots.length" class="opening-shot-list" data-testid="video-plan-shots">
      <li
        v-for="shot in shots"
        :key="shot.id || shot.shot_id"
        class="opening-shot-item"
        :class="{ 'opening-shot-item--block': isOpeningShot(shot, opening) }"
        :data-testid="isOpeningShot(shot, opening) ? 'video-plan-opening-shot' : `video-plan-shot-${shot.id || shot.shot_id}`"
      >
        <strong>{{ isOpeningShot(shot, opening) ? '开头连续块' : (shot.id || shot.shot_id) }}</strong>
        <span v-if="shotDuration(shot)">{{ formatDuration(shotDuration(shot)) }}</span>
        <em v-if="shot.action">{{ shot.action }}</em>
      </li>
    </ol>
    <dl v-if="intent.goal || intent.audience || intent.promise" class="intent-grid">
      <div v-if="intent.goal">
        <dt>目标</dt>
        <dd>{{ cleanDisplayText(intent.goal) || '—' }}</dd>
      </div>
      <div v-if="intent.audience">
        <dt>受众</dt>
        <dd>{{ cleanDisplayText(intent.audience) || '—' }}</dd>
      </div>
      <div v-if="intent.promise">
        <dt>承诺</dt>
        <dd>{{ cleanDisplayText(intent.promise) || '—' }}</dd>
      </div>
    </dl>
    <ul v-if="references.length" class="reference-list">
      <li v-for="item in references" :key="item.path || item">
        <strong :title="item.path">{{ referenceLabel(item.path || item) }}</strong>
        <span v-if="item.why">{{ cleanDisplayText(item.why) }}</span>
      </li>
    </ul>
    <div v-if="skeleton.one_liner || skeleton.hook || skeleton.value || skeleton.cta" class="skeleton-block">
      <div v-if="cleanDisplayText(skeleton.one_liner)" class="skeleton-oneliner">
        <span>一行骨架</span>
        <p>{{ cleanDisplayText(skeleton.one_liner) }}</p>
      </div>
      <dl class="skeleton-beats">
        <div>
          <dt>Hook</dt>
          <dd>{{ cleanDisplayText(skeleton.hook) || '—' }}</dd>
        </div>
        <div>
          <dt>Value</dt>
          <dd>{{ cleanDisplayText(skeleton.value) || '—' }}</dd>
        </div>
        <div>
          <dt>CTA</dt>
          <dd>{{ cleanDisplayText(skeleton.cta) || '—' }}</dd>
        </div>
      </dl>
    </div>
    <p v-if="replacePlan" class="page-muted" data-testid="video-replace-plan">
      本轮新生成：{{ resolved.join('、') || '无' }}
    </p>
  </section>
</template>
