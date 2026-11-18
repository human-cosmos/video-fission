<script setup>
import { computed } from 'vue'
import { STAGE_LABELS, STAGE_ORDER, STATUS_LABELS } from '../utils/format'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
  cancelling: { type: Boolean, default: false },
})

defineEmits(['cancel'])

const status = computed(() => props.payload?.status || '')
const currentStage = computed(() => props.payload?.current_stage || '')
const isRunning = computed(() => status.value === 'running')
const stages = computed(() => {
  const provided = props.payload?.stages
  if (Array.isArray(provided) && provided.length) return provided
  const active = currentStage.value
  const activeIndex = STAGE_ORDER.indexOf(active)
  return STAGE_ORDER.map((id, index) => ({
    id,
    label: STAGE_LABELS[id] || id,
    state:
      status.value === 'failed' && id === active
        ? 'failed'
        : active === 'done' || (activeIndex >= 0 && index < activeIndex)
          ? 'done'
          : id === active
            ? 'active'
            : 'pending',
  }))
})
const warnings = computed(() => {
  const value = props.payload?.warnings || props.payload?.warning
  if (!value) return []
  return Array.isArray(value) ? value : [value]
})
</script>

<template>
  <section class="video-card studio-card" data-testid="video-progress-card">
    <div class="status-row">
      <strong>{{ STATUS_LABELS[status] || status || '任务进度' }}</strong>
      <span v-if="payload.production_id" data-testid="video-production-id">{{ payload.production_id }}</span>
    </div>
    <ol class="stage-rail">
      <li v-for="stage in stages" :key="stage.id" :class="stage.state">
        <i></i>
        {{ stage.label }}
      </li>
    </ol>
    <p v-if="payload.error" class="page-error">{{ payload.error }}</p>
    <ul v-if="warnings.length" class="warning-list">
      <li v-for="(warning, index) in warnings" :key="index">{{ warning }}</li>
    </ul>
    <div v-if="isRunning" class="studio-actions">
      <button
        class="button subtle"
        type="button"
        data-testid="video-cancel"
        :disabled="cancelling"
        @click="$emit('cancel', payload.production_id)"
      >
        取消任务
      </button>
    </div>
  </section>
</template>
