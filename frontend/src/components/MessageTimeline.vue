<script setup>
import { ChevronRight, CircleAlert, Copy, FileText, Terminal, Wrench } from '@lucide/vue'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import {
  agentMessageStatusText,
  canShowTraceOutput,
  groupExecutionItems,
  isAgentCommentary,
  itemLabel,
  payloadText,
  toolCallName,
} from '../utils/events'
import { isVideoFile } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'
import { mediaUrl } from '../utils/productionMedia'
import { toolLabel } from '../utils/agentTraceDisplay'
import AspectVideo from './AspectVideo.vue'
import VideoObserveCard from './VideoObserveCard.vue'
import VideoOutputCard from './VideoOutputCard.vue'
import VideoPlanCard from './VideoPlanCard.vue'
import VideoProgressCard from './VideoProgressCard.vue'
import VideoSegmentsCard from './VideoSegmentsCard.vue'
import VideoUnderstandingCard from './VideoUnderstandingCard.vue'

const props = defineProps({
  conversation: { type: Object, default: null },
  rerunning: { type: Boolean, default: false },
  selecting: { type: Boolean, default: false },
  cancelling: { type: Boolean, default: false },
})

const emit = defineEmits([
  'decide-approval',
  'answer-clarification',
  'copy',
  'cancel-production',
  'rerun-selected',
  'rerun-brief',
  'select-video',
  'regenerate-scripts',
  'regenerate-video',
  'use-prompt',
])
const scroller = ref(null)
const clarificationDrafts = reactive({})
const displayItems = computed(() => groupExecutionItems(props.conversation?.items || []))
const starterTickets = [
  {
    title: '参考视频拆解',
    desc: '看懂卖点、节奏、镜头与叙事结构',
    prompt: '帮我拆解一个参考视频：它为什么抓人，哪些结构可以复用？',
  },
  {
    title: '短视频脚本',
    desc: '保留有效结构，换一个更有记忆点的开场',
    prompt: '把一个产品卖点拆成 30 秒短视频，先给我 3 个不同方向的开场。',
  },
  {
    title: '镜头级规划',
    desc: '把脚本拆成可执行的镜头、素材与生成要求',
    prompt: '把这条短视频脚本拆成镜头级执行方案，并标出每个镜头的素材需求。',
  },
]
const starterAsks = [
  ['卖点拆解', '把一个产品卖点拆成 30 秒短视频'],
  ['开场钩子', '为美妆新品写一个反常识开场'],
  ['脚本结构', '设计一套三段式种草脚本'],
  ['镜头复盘', '复盘这条视频的镜头节奏和转场问题'],
]

function scrollToBottom(behavior = 'smooth') {
  nextTick(() => {
    if (scroller.value) {
      scroller.value.scrollTo({
        top: scroller.value.scrollHeight,
        behavior,
      })
    }
  })
}

function approvalDetail(approval) {
  const request = approval.request || {}
  if (request.kind === 'command') {
    return [request.command, request.cwd].filter(Boolean).join('\n')
  }
  if (request.paths?.length) return request.paths.join('\n')
  return JSON.stringify(request, null, 2)
}

function questionOptions(question) {
  const values = question.options || []
  return values.map((option) =>
    typeof option === 'string'
      ? option
      : option.label || option.value || option.const || String(option),
  )
}

function submitClarification(clarification) {
  const answers = {}
  for (const question of clarification.questions || []) {
    const value = clarificationDrafts[question.id]
    if (question.required && !String(value || '').trim()) return
    if (value !== undefined && String(value).trim()) answers[question.id] = value
  }
  emit('answer-clarification', { id: clarification.id, answers })
}

function copyText(text) {
  emit('copy', text)
}

function cardPayload(item) {
  const payload = item?.payload || {}
  if (payload.status) return payload
  const productionId = payload.production_id
  if (!productionId) return payload
  const progress = (props.conversation?.items || []).find(
    (other) =>
      other.item_type === 'video_progress' && other.payload?.production_id === productionId,
  )
  return progress?.payload?.status
    ? { ...payload, status: progress.payload.status, updated_at: progress.payload.updated_at }
    : payload
}

function structuredText(item) {
  const payload = item.payload || {}
  const value = payload.output || payload.patch || payload.text || ''
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function traceStatusText(item) {
  if (item.status === 'inProgress') return '进行中'
  if (item.status === 'failed') return '有失败'
  return '已完成'
}

function executionNames(item) {
  return [...new Set(item.items.map((step) => toolLabel(toolCallName(step))).filter(Boolean))]
}

function stepStatusText(status) {
  if (status === 'inProgress') return '进行中'
  if (status === 'failed') return '失败'
  return '完成'
}

watch(
  () => props.conversation?.items?.length,
  (next, previous) => {
    if (next !== previous) scrollToBottom()
  },
)

watch(
  () => props.conversation?.id,
  () => scrollToBottom('instant'),
)

defineExpose({ scrollToBottom })
</script>

<template>
  <div ref="scroller" class="message-scroller">
    <div v-if="!conversation" class="welcome">
      <header class="welcome-head">
        <h2>先看再拆</h2>
        <p>
          从参考视频、卖点和目标受众出发，先拿到判断，再进入脚本与镜头。上传素材、追问方向、继续执行，上下文都留在这张工作台上。
        </p>
        <div class="welcome-signal" aria-label="Video Fission 工作范围">
          <span><i></i>视频理解</span>
          <span><i></i>脚本规划</span>
          <span><i></i>镜头拆解</span>
          <span><i></i>成片生成</span>
        </div>
      </header>

      <div class="tickets" role="list">
        <button
          v-for="(ticket, index) in starterTickets"
          :key="ticket.title"
          type="button"
          class="ticket"
          role="listitem"
          @click="$emit('use-prompt', ticket.prompt)"
        >
          <span class="ticket-index">0{{ index + 1 }}</span>
          <span class="ticket-body">
            <span class="ticket-name">{{ ticket.title }}</span>
            <span class="ticket-desc">{{ ticket.desc }}</span>
          </span>
          <span class="ticket-arrow" aria-hidden="true">↗</span>
        </button>
      </div>

      <div class="asks">
        <p class="asks-title">推荐指令</p>
        <button
          v-for="item in starterAsks"
          :key="item[0]"
          type="button"
          class="ask"
          @click="$emit('use-prompt', item[1])"
        >
          {{ item[1] }}
        </button>
      </div>
    </div>

    <div v-else class="transcript" data-testid="message-timeline">
      <article
        v-for="(item, index) in displayItems"
        :key="item.id"
        class="message"
        :class="[
          `message--${item.item_type}`,
          { 'message--streaming': item.status === 'inProgress' },
        ]"
        :style="{ '--line-hue': `${index * 7 + 18}deg` }"
      >
        <div class="message-spine" aria-hidden="true">
          <span></span>
        </div>

        <template v-if="item.item_type === 'userMessage'">
          <div class="user-message" data-testid="user-message">
            <div class="user-label">你的问题</div>
            <p>{{ item.payload?.text }}</p>
            <div v-if="item.payload?.mentions?.length" class="mention-row">
              <span v-for="mention in item.payload.mentions" :key="mention.path">
                @{{ mention.name }}
              </span>
            </div>
            <div v-if="item.payload?.files?.length" class="attachment-row">
              <template v-for="file in item.payload.files" :key="file.id || file.path || file.name">
                <AspectVideo
                  v-if="isVideoFile(file) && mediaUrl(file)"
                  class="user-video"
                  :src="mediaUrl(file)"
                  :width="Number(file.width) || 0"
                  :height="Number(file.height) || 0"
                  testid="user-message-video"
                  compact
                />
                <span v-else>
                  <FileText :size="13" /> {{ file.name }}
                </span>
              </template>
            </div>
          </div>
        </template>

        <template v-else-if="item.item_type === 'agentMessage'">
          <div
            class="agent-message"
            :class="{
              'agent-message--commentary': isAgentCommentary(item),
              'agent-message--final': !isAgentCommentary(item),
            }"
          >
            <div v-if="isAgentCommentary(item)" class="agent-progress-head">
              <span class="agent-progress-label">处理进度</span>
              <span
                class="memo-status"
                :class="{ live: item.status === 'inProgress' }"
              >{{ agentMessageStatusText(item) }}</span>
            </div>
            <div v-else class="memo-head">
              <span class="memo-agent-mark">VF</span>
              <span class="memo-agent-copy">
                <strong>Video Fission</strong>
                <small>视频拆解与创作智能体</small>
              </span>
              <span
                class="memo-status"
                :class="{ live: item.status === 'inProgress' }"
              >{{ agentMessageStatusText(item) }}</span>
            </div>
            <div class="markdown-body" v-html="renderMarkdown(payloadText(item))"></div>
            <span v-if="item.status === 'inProgress'" class="typing-caret"></span>
            <footer
              v-if="payloadText(item)"
              class="memo-foot"
              :class="{ 'memo-foot--commentary': isAgentCommentary(item) }"
            >
              <button
                class="memo-action"
                type="button"
                title="复制回复"
                @click="copyText(payloadText(item))"
              >
                <Copy :size="13" />
                复制
              </button>
            </footer>
          </div>
        </template>

        <template v-else-if="item.item_type === 'executionGroup'">
          <details
            class="execution-group"
            data-testid="execution-group"
            :open="item.status === 'inProgress'"
          >
            <summary>
              <ChevronRight :size="14" />
              <span>执行轨迹</span>
              <span class="execution-names">
                <code v-for="name in executionNames(item)" :key="name">{{ name }}</code>
              </span>
              <small>{{ item.items.length }} 步 · {{ traceStatusText(item) }}</small>
            </summary>
            <div class="execution-steps">
              <details
                v-for="step in item.items"
                :key="step.id"
                class="execution-step"
                :class="{ 'has-error': step.status === 'failed' }"
                :open="step.status === 'inProgress'"
              >
                <summary>
                  <Terminal v-if="step.item_type === 'commandExecution'" :size="13" />
                  <Wrench v-else :size="13" />
                  <span>{{ itemLabel(step.item_type) }}</span>
                  <code>{{ toolLabel(toolCallName(step)) }}</code>
                  <small>{{ stepStatusText(step.status) }}</small>
                </summary>
                <pre v-if="canShowTraceOutput(step) && structuredText(step)">{{ structuredText(step) }}</pre>
                <p v-else-if="step.item_type === 'reasoning'">{{ payloadText(step) }}</p>
              </details>
            </div>
          </details>
        </template>

        <template v-else-if="item.item_type === 'reasoning'">
          <details class="trace-block trace-block--reasoning">
            <summary>
              <ChevronRight :size="14" />
              <span>推理轨迹</span>
              <small>{{ item.status === 'inProgress' ? '进行中' : '已折叠' }}</small>
            </summary>
            <p>{{ payloadText(item) }}</p>
          </details>
        </template>

        <template v-else-if="item.item_type === 'plan'">
          <div class="plan-block">
            <div class="trace-label">PLAN / 计划</div>
            <div class="markdown-body" v-html="renderMarkdown(payloadText(item))"></div>
          </div>
        </template>

        <template v-else-if="item.item_type === 'video_progress'">
          <VideoProgressCard
            :payload="item.payload"
            :cancelling="cancelling"
            @cancel="$emit('cancel-production', $event)"
          />
        </template>

        <template v-else-if="item.item_type === 'video_understanding'">
          <VideoUnderstandingCard :payload="cardPayload(item)" />
        </template>

        <template v-else-if="item.item_type === 'video_plan'">
          <VideoPlanCard
            :payload="cardPayload(item)"
            :rerunning="rerunning"
            @regenerate-scripts="$emit('regenerate-scripts', $event)"
            @regenerate-video="$emit('regenerate-video', $event)"
          />
        </template>

        <template v-else-if="item.item_type === 'video_segments'">
          <VideoSegmentsCard
            :payload="cardPayload(item)"
            :rerunning="rerunning"
            @rerun-selected="$emit('rerun-selected', $event)"
            @rerun-brief="$emit('rerun-brief', $event)"
          />
        </template>

        <template v-else-if="item.item_type === 'video_observe'">
          <VideoObserveCard :payload="cardPayload(item)" />
        </template>

        <template v-else-if="item.item_type === 'video_output'">
          <VideoOutputCard
            :payload="cardPayload(item)"
            :selecting="selecting"
            @select-video="$emit('select-video', $event)"
          />
        </template>

        <template
          v-else-if="['commandExecution', 'functionCallOutput', 'dynamicToolCall', 'mcpToolCall', 'webSearch', 'fileChange'].includes(item.item_type)"
        >
          <details class="trace-block" :open="item.status === 'inProgress'">
            <summary>
              <Terminal v-if="item.item_type === 'commandExecution'" :size="14" />
              <Wrench v-else :size="14" />
              <span>{{ itemLabel(item.item_type) }}</span>
              <code>{{ toolLabel(toolCallName(item)) }}</code>
              <small>{{ traceStatusText(item) }}</small>
            </summary>
            <pre v-if="canShowTraceOutput(item) && structuredText(item)">{{ structuredText(item) }}</pre>
          </details>
        </template>

        <template v-else>
          <details class="trace-block">
            <summary>
              <Wrench :size="14" />
              <span>{{ itemLabel(item.item_type) }}</span>
              <small>{{ traceStatusText(item) }}</small>
            </summary>
            <pre>{{ JSON.stringify(item.payload, null, 2) }}</pre>
          </details>
        </template>
      </article>

      <article
        v-for="approval in conversation.pending_approvals || []"
        :key="approval.id"
        class="message interaction-message"
      >
        <div class="message-spine"><span></span></div>
        <div class="interaction-card approval-card" data-testid="approval-card">
          <div class="interaction-kicker"><CircleAlert :size="15" /> 需要你的批准</div>
          <h3>{{ approval.request?.reason || '智能中枢请求继续执行' }}</h3>
          <pre>{{ approvalDetail(approval) }}</pre>
          <div class="interaction-actions">
            <button class="button primary" @click="$emit('decide-approval', approval.id, 'accept')">
              允许本次
            </button>
            <button class="button subtle" @click="$emit('decide-approval', approval.id, 'decline')">
              拒绝
            </button>
          </div>
        </div>
      </article>

      <article
        v-for="clarification in conversation.pending_clarifications || []"
        :key="clarification.id"
        class="message interaction-message"
      >
        <div class="message-spine"><span></span></div>
        <form class="interaction-card clarification-card" @submit.prevent="submitClarification(clarification)">
          <div class="interaction-kicker">AGENT QUESTION</div>
          <div v-for="question in clarification.questions" :key="question.id" class="question-row">
            <label :for="`${clarification.id}-${question.id}`">
              {{ question.header || question.question }}
            </label>
            <small v-if="question.question && question.question !== question.header">
              {{ question.question }}
            </small>
            <select
              v-if="questionOptions(question).length"
              :id="`${clarification.id}-${question.id}`"
              v-model="clarificationDrafts[question.id]"
            >
              <option value="">请选择</option>
              <option v-for="option in questionOptions(question)" :key="option" :value="option">
                {{ option }}
              </option>
            </select>
            <input
              v-else
              :id="`${clarification.id}-${question.id}`"
              v-model="clarificationDrafts[question.id]"
              :required="question.required"
              placeholder="输入回答"
            />
          </div>
          <div class="interaction-actions">
            <button class="button primary" type="submit">提交回答</button>
          </div>
        </form>
      </article>

      <div v-if="conversation.status === 'running'" class="thinking-line">
        <span></span>
        <p>正在沿执行轨迹推进</p>
      </div>

      <div v-if="conversation.last_error" class="error-banner" data-testid="conversation-error">
        <CircleAlert :size="16" />
        <span>{{ conversation.last_error }}</span>
      </div>
    </div>

    <button
      v-if="conversation?.items?.length"
      class="scroll-bottom"
      title="滚动到底部"
      @click="scrollToBottom()"
    >
      ↓
    </button>
  </div>
</template>

