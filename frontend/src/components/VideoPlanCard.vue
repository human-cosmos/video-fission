<script setup>
import { computed } from 'vue'
import { cleanDisplayText, referenceLabel } from '../utils/format'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
})

const intent = computed(() => props.payload?.intent || {})
const skeleton = computed(() => props.payload?.skeleton || {})
const references = computed(() => intent.value?.references || props.payload?.references || [])
const replacePlan = computed(() => props.payload?.replace_plan || null)
const resolved = computed(() => replacePlan.value?.resolved_segment_ids || [])
</script>

<template>
  <section class="video-card studio-card" data-testid="video-plan-card">
    <h3>任务规划</h3>
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
