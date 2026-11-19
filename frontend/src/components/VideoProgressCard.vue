<script setup>
import { Check, ChevronRight, LoaderCircle, X } from '@lucide/vue'
import { computed } from 'vue'
import { STAGE_LABELS, STAGE_ORDER, STATUS_LABELS } from '../utils/format'
import {
  agentTraceItems,
  agentTraceSummary,
  currentAgentActionText,
  hasAgentTrace,
} from '../utils/agentTraceDisplay'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
  cancelling: { type: Boolean, default: false },
})

defineEmits(['cancel'])

const status = computed(() => props.payload?.status || '')
const currentStage = computed(() => props.payload?.current_stage || '')
const isRunning = computed(() => status.value === 'running')
const awaitingCreativeContext = computed(() => status.value === 'awaiting_creative_context')
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
const pipelineStages = computed(() => stages.value.filter((stage) => stage.id !== 'done'))
const completedStages = computed(() =>
  pipelineStages.value.filter((stage) => stage.state === 'done'),
)
const activeStage = computed(
  () =>
    pipelineStages.value.find((stage) =>
      ['active', 'awaiting', 'failed', 'cancelled'].includes(stage.state),
    ) || null,
)
const futureStages = computed(() =>
  pipelineStages.value.filter((stage) => stage.state === 'pending'),
)
const activeStageIndex = computed(() =>
  activeStage.value
    ? pipelineStages.value.findIndex((stage) => stage.id === activeStage.value.id) + 1
    : 0,
)
const allStagesDone = computed(
  () =>
    pipelineStages.value.length > 0 &&
    pipelineStages.value.every((stage) => stage.state === 'done'),
)
const showAgentTrace = computed(() => hasAgentTrace(props.payload))
const traceItems = computed(() => agentTraceItems(props.payload))
const traceSummary = computed(() => agentTraceSummary(props.payload))
const agentActionText = computed(() => currentAgentActionText(props.payload))
const statusTitle = computed(() => {
  if (awaitingCreativeContext.value) return '参考拆解已完成，等待生成创意脚本'
  if (awaitingScript.value) return '等待选择创意脚本'
  if (showAgentTrace.value && agentActionText.value) {
    if (status.value === 'failed') return agentActionText.value
    if (status.value === 'running') return agentActionText.value
  }
  if (status.value === 'failed') {
    return activeStage.value ? `${activeStage.value.label}阶段失败` : '视频任务执行失败'
  }
  if (status.value === 'cancelled') return '视频任务已取消'
  if (status.value === 'completed' || allStagesDone.value) return '视频任务已完成'
  if (status.value !== 'running') return STATUS_LABELS[status.value] || status.value || '任务进度'

  return {
    ingest: '正在接入参考素材',
    decompose: '正在拆解参考视频',
    intent: '正在提炼创作意图',
    script: '正在生成创意脚本',
    generate: '正在生成视频片段',
    compose: '正在合成最终成片',
  }[currentStage.value] || '正在推进视频任务'
})
const stageProgressText = computed(() => {
  if (showAgentTrace.value && traceSummary.value) return traceSummary.value
  if (allStagesDone.value) return `${pipelineStages.value.length} 个阶段已完成`
  if (!activeStage.value) return STATUS_LABELS[status.value] || '等待下一阶段'
  return `${activeStage.value.label} · ${activeStageIndex.value}/${pipelineStages.value.length}`
})
const futureStagePath = computed(() =>
  futureStages.value.map((stage) => stage.label).join(' → '),
)
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

function stageSummary(stage) {
  const outputs = (stage.outputs || [])
    .map((row) => row.value)
    .filter(Boolean)
  if (outputs.length) return outputs.join(' · ')
  return stage.description || '阶段已完成'
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
  <section
    class="video-card studio-card"
    :class="`production-status-${status || 'pending'}`"
    data-testid="video-progress-card"
  >
    <header class="production-status-row">
      <div class="production-status-main">
        <span class="production-status-signal" aria-hidden="true"></span>
        <span class="production-status-copy">
          <strong>{{ statusTitle }}</strong>
          <small>{{ stageProgressText }}</small>
        </span>
      </div>
      <span v-if="payload.production_id" class="production-reference">
        <span data-testid="video-production-id">{{ payload.production_id }}</span>
        <time v-if="formatClock(payload.updated_at)">{{ formatClock(payload.updated_at) }}</time>
      </span>
    </header>
    <ol class="stage-rail" aria-label="视频任务阶段">
      <li v-for="stage in pipelineStages" :key="stage.id" :class="stage.state">
        <i></i>
        <span>{{ stage.label }}</span>
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
    <section v-if="showAgentTrace" class="agent-trace" data-testid="video-progress-agent-trace">
      <header class="agent-trace-head">
        <strong>执行轨迹</strong>
        <span v-if="traceSummary">{{ traceSummary }}</span>
      </header>
      <ol class="agent-trace-list">
        <li
          v-for="item in traceItems"
          :key="item.key"
          :class="`agent-trace-item agent-trace-item--${item.kind} is-${item.state}`"
          :data-testid="item.kind === 'plan' ? 'video-progress-agent-plan' : `video-progress-agent-${item.kind}-${item.name}`"
        >
          <span class="agent-trace-kind">{{ item.kind === 'plan' ? '计划' : (item.kind === 'skill' ? 'Skill' : '工具') }}</span>
          <strong>{{ item.label }}</strong>
          <small v-if="item.kind === 'plan'">{{ item.detail }}</small>
          <code v-else-if="item.detail">{{ item.detail }}</code>
          <em v-if="item.state === 'failed'">失败</em>
          <em v-else-if="item.state === 'loaded'">已加载</em>
          <em v-else-if="item.state === 'done'">完成</em>
        </li>
      </ol>
    </section>

    <details v-if="allStagesDone" class="production-completed-summary">
      <summary>
        <span class="production-completed-check"><Check :size="13" /></span>
        <span>
          <strong>全部阶段已完成</strong>
          <small>查看阶段明细与执行记录</small>
        </span>
        <ChevronRight :size="15" />
      </summary>
      <ol class="production-step-list" data-testid="video-production-steps">
        <li
          v-for="stage in completedStages"
          :key="stage.id"
          class="production-step is-done production-step--completed"
          :data-testid="`video-production-step-${stage.id}`"
        >
          <details class="production-step-disclosure">
            <summary>
              <span class="production-step-marker" aria-hidden="true">
                <Check :size="12" />
              </span>
              <span class="production-step-summary">
                <span class="production-step-heading">
                  <strong>{{ stage.label }}</strong>
                  <span>{{ stageStatusText(stage.state) }}</span>
                </span>
                <span class="production-step-compact">{{ stageSummary(stage) }}</span>
              </span>
              <ChevronRight :size="14" />
            </summary>
            <div class="production-step-content">
              <p v-if="stage.description" class="production-step-description">
                {{ stage.description }}
              </p>
              <dl v-if="stage.outputs?.length" class="production-step-output">
                <div v-for="row in stage.outputs" :key="row.label">
                  <dt>{{ row.label }}</dt>
                  <dd>{{ row.value }}</dd>
                </div>
              </dl>
              <details v-if="stage.activities?.length" class="production-step-log">
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
          </details>
        </li>
      </ol>
    </details>

    <ol v-else class="production-step-list" data-testid="video-production-steps">
      <li
        v-for="stage in completedStages"
        :key="stage.id"
        class="production-step is-done production-step--completed"
        :data-testid="`video-production-step-${stage.id}`"
      >
        <details class="production-step-disclosure">
          <summary>
            <span class="production-step-marker" aria-hidden="true">
              <Check :size="12" />
            </span>
            <span class="production-step-summary">
              <span class="production-step-heading">
                <strong>{{ stage.label }}</strong>
                <span>{{ stageStatusText(stage.state) }}</span>
              </span>
              <span class="production-step-compact">{{ stageSummary(stage) }}</span>
            </span>
            <ChevronRight :size="14" />
          </summary>
          <div class="production-step-content">
            <p v-if="stage.description" class="production-step-description">
              {{ stage.description }}
            </p>
            <dl v-if="stage.outputs?.length" class="production-step-output">
              <div v-for="row in stage.outputs" :key="row.label">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </div>
            </dl>
            <details v-if="stage.activities?.length" class="production-step-log">
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
        </details>
      </li>

      <li
        v-if="activeStage"
        class="production-step production-step--active"
        :class="`is-${activeStage.state}`"
        :data-testid="`video-production-step-${activeStage.id}`"
      >
        <span class="production-step-marker" aria-hidden="true">
          <LoaderCircle
            v-if="activeStage.state === 'active' && !currentActivity"
            class="is-spinning"
            :size="13"
          />
          <X v-else-if="activeStage.state === 'failed' || activeStage.state === 'cancelled'" :size="13" />
          <span v-else class="production-step-dot"></span>
        </span>
        <div class="production-step-content">
          <div class="production-step-heading">
            <strong>{{ activeStage.label }}</strong>
            <span>{{ stageStatusText(activeStage.state) }}</span>
          </div>
          <p v-if="activeStage.description" class="production-step-description">
            {{ activeStage.description }}
          </p>
          <p
            v-if="
              activeStage.state === 'awaiting' ||
              (activeStage.state === 'active' && !currentActivity)
            "
            class="production-step-pending"
          >
            {{ activeStage.state === 'awaiting' ? '等待选择脚本后继续' : '正在处理，结果完成后显示' }}
          </p>
          <dl v-if="activeStage.outputs?.length" class="production-step-output">
            <div v-for="row in activeStage.outputs" :key="row.label">
              <dt>{{ row.label }}</dt>
              <dd>{{ row.value }}</dd>
            </div>
          </dl>
          <details v-if="activeStage.activities?.length" class="production-step-log">
            <summary>
              <ChevronRight :size="13" />
              执行记录
              <small>{{ activeStage.activities.length }}</small>
            </summary>
            <ol>
              <li
                v-for="activity in activeStage.activities"
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

      <li v-if="futureStages.length" class="production-step production-step--upcoming">
        <span class="production-step-marker" aria-hidden="true">{{ futureStages.length }}</span>
        <div class="production-step-content">
          <div class="production-step-heading">
            <strong>后续阶段</strong>
            <span>自动推进</span>
          </div>
          <p class="production-step-description">{{ futureStagePath }}</p>
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
