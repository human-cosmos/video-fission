<script setup>
import { Check, ChevronRight, LoaderCircle, X } from '@lucide/vue'
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
const awaitingScript = computed(() => status.value === 'awaiting_script_choice')
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
const currentActivity = computed(() => {
  if (!isRunning.value) return null
  const items = (props.payload?.activity || []).filter(
    (item) => item?.stage === currentStage.value,
  )
  return items[items.length - 1] || null
})
const warnings = computed(() => {
  const value = props.payload?.warnings || props.payload?.warning
  if (!value) return []
  return Array.isArray(value) ? value : [value]
})

function stageStatusText(state) {
  return {
    done: '已完成',
    active: '进行中',
    awaiting: '等待选择',
    failed: '失败',
    cancelled: '已取消',
    pending: '待执行',
  }[state] || '待执行'
}

function formatClock(value) {
  if (!value) return ''
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
</script>

<template>
  <section class="video-card studio-card" data-testid="video-progress-card">
    <div class="status-row">
      <strong>{{ STATUS_LABELS[status] || status || '任务进度' }}</strong>
      <span v-if="payload.production_id" class="production-reference">
        <span data-testid="video-production-id">{{ payload.production_id }}</span>
        <time v-if="formatClock(payload.updated_at)">{{ formatClock(payload.updated_at) }}</time>
      </span>
    </div>
    <ol class="stage-rail">
      <li v-for="stage in stages" :key="stage.id" :class="stage.state">
        <i></i>
        {{ stage.label }}
      </li>
    </ol>
    <div
      v-if="isRunning && currentActivity"
      class="production-current-operation"
      data-testid="video-current-operation"
    >
      <LoaderCircle class="is-spinning" :size="15" />
      <span>{{ currentActivity.text }}</span>
      <small v-if="currentActivity.detail">{{ currentActivity.detail }}</small>
    </div>
    <ol class="production-step-list" data-testid="video-production-steps">
      <li
        v-for="(stage, index) in stages"
        :key="stage.id"
        class="production-step"
        :class="[`is-${stage.state}`, { 'has-output': stage.outputs?.length }]"
        :data-testid="`video-production-step-${stage.id}`"
      >
        <span class="production-step-marker" aria-hidden="true">
          <Check v-if="stage.state === 'done'" :size="13" />
          <LoaderCircle v-else-if="stage.state === 'active'" class="is-spinning" :size="13" />
          <X v-else-if="stage.state === 'failed' || stage.state === 'cancelled'" :size="13" />
          <template v-else>{{ index + 1 }}</template>
        </span>
        <div class="production-step-content">
          <div class="production-step-heading">
            <strong>{{ stage.label }}</strong>
            <span>{{ stageStatusText(stage.state) }}</span>
          </div>
          <p class="production-step-description">{{ stage.description }}</p>
          <dl v-if="stage.outputs?.length" class="production-step-output">
            <div v-for="row in stage.outputs" :key="row.label">
              <dt>{{ row.label }}</dt>
              <dd>{{ row.value }}</dd>
            </div>
          </dl>
          <p
            v-else-if="stage.state === 'active' || stage.state === 'awaiting'"
            class="production-step-pending"
          >
            {{ stage.state === 'awaiting' ? '等待选择脚本后继续' : '正在处理，结果完成后显示' }}
          </p>
          <details
            v-if="stage.activities?.length"
            class="production-step-log"
            :open="stage.state === 'active' || stage.state === 'failed'"
          >
            <summary>
              <ChevronRight :size="13" />
              执行记录
              <small>{{ stage.activities.length }}</small>
            </summary>
            <ol>
              <li
                v-for="activity in stage.activities"
                :key="`${activity.id}-${activity.created_at}`"
                :class="`tone-${activity.tone}`"
              >
                <time>{{ formatClock(activity.created_at) }}</time>
                <div>
                  <span>{{ activity.text }}</span>
                  <small v-if="activity.detail">{{ activity.detail }}</small>
                </div>
              </li>
            </ol>
          </details>
        </div>
      </li>
    </ol>
    <p
      v-if="awaitingScript"
      class="progress-awaiting-script"
      data-testid="video-progress-awaiting-script"
    >请选择创意脚本</p>
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
