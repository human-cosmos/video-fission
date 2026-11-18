<script setup>
import {
  Activity,
  Archive,
  ArrowUp,
  Check,
  GitBranch,
  Menu,
  MoreHorizontal,
  Paperclip,
  PanelRight,
  RefreshCw,
  Settings2,
  Square,
  Trash2,
  X,
  Zap,
} from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, eventUrl } from './api'
import BrandLockup from './components/BrandLockup.vue'
import CapabilityPanel from './components/CapabilityPanel.vue'
import ConversationSidebar from './components/ConversationSidebar.vue'
import MessageTimeline from './components/MessageTimeline.vue'
import ModelSettings from './components/ModelSettings.vue'
import SkillEditorModal from './components/SkillEditorModal.vue'
import TopbarSelect from './components/TopbarSelect.vue'
import VideoStudio from './components/VideoStudio.vue'
import WorkspaceModeNav from './components/WorkspaceModeNav.vue'
import { isRefreshEvent } from './utils/events'
import { WORKSPACE_MODES } from './utils/format'

const conversations = ref([])
const currentConversation = ref(null)
const models = ref([])
const skills = ref([])
const tools = ref([])
const loading = ref(true)
const listLoading = ref(false)
const bootError = ref('')
const composer = ref('')
const attachments = ref([])
const skillMentions = ref([])
const uploading = ref(false)
const sending = ref(false)
const showArchived = ref(false)
const mobileSidebar = ref(false)
const mobileCapabilities = ref(false)
const skillModalOpen = ref(false)
const editingSkill = ref(null)
const skillBusy = ref(false)
const toast = ref('')
const streamConnected = ref(false)
const timeline = ref(null)
const composerInput = ref(null)
let eventSource = null
let refreshTimer = null
let refreshInFlight = false
let refreshQueued = false
let selectionSequence = 0
let uploadGeneration = 0
let toastTimer = null
let titleBeforeEdit = ''

const MODE_STORAGE_KEY = 'video-fission.workspaceMode'
const MODE_IDS = WORKSPACE_MODES.map((item) => item.id)

function storedWorkspaceMode() {
  const stored = window.localStorage.getItem(MODE_STORAGE_KEY)
  return MODE_IDS.includes(stored) ? stored : 'chat'
}

const workspaceMode = ref(storedWorkspaceMode())
const isChatMode = computed(() => workspaceMode.value === 'chat')

watch(workspaceMode, (mode) => {
  window.localStorage.setItem(MODE_STORAGE_KEY, mode)
  if (mode !== 'chat') {
    mobileSidebar.value = false
    mobileCapabilities.value = false
  }
})

let sidebarResizeStartX = 0
let sidebarResizeStartWidth = 0

const SIDEBAR_MIN_WIDTH = 240
const SIDEBAR_MAX_WIDTH = 420
const SIDEBAR_DEFAULT_WIDTH = 278
const SIDEBAR_COLLAPSED_WIDTH = 68

function clampSidebarWidth(value) {
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(value)))
}

function storedSidebarWidth() {
  const stored = window.localStorage.getItem('video-fission.sidebarWidth')
  if (!stored) return SIDEBAR_DEFAULT_WIDTH
  const value = Number(stored)
  return Number.isFinite(value) ? clampSidebarWidth(value) : SIDEBAR_DEFAULT_WIDTH
}

const sidebarWidth = ref(storedSidebarWidth())
const sidebarCollapsed = ref(
  window.localStorage.getItem('video-fission.sidebarCollapsed') === 'true',
)
const sidebarGridWidth = computed(() =>
  sidebarCollapsed.value ? SIDEBAR_COLLAPSED_WIDTH : sidebarWidth.value,
)

const currentModel = computed(() =>
  models.value.find((model) => model.id === currentConversation.value?.model),
)
const supportedEfforts = computed(
  () => currentModel.value?.supported_reasoning_efforts || ['low', 'medium', 'high'],
)
const effortDetails = {
  none: '最快响应，适合简单整理',
  minimal: '轻量推理，优先速度',
  low: '快速完成，适合明确任务',
  medium: '速度与推演深度平衡',
  high: '更充分拆解复杂创作任务',
  xhigh: '高强度推演，适合长链路任务',
  max: '最高推理深度',
  ultra: '极限推理，执行时间更长',
}
const modelOptions = computed(() =>
  models.value.map((model) => ({
    value: model.id,
    label: model.name,
    detail: model.description,
    meta: model.is_default ? '默认' : '',
  })),
)
const effortOptions = computed(() =>
  supportedEfforts.value.map((effort) => ({
    value: effort,
    label: String(effort).toUpperCase(),
    detail: effortDetails[effort] || '',
  })),
)
const visibleConversations = computed(() =>
  conversations.value.filter((item) =>
    showArchived.value ? item.is_archived : !item.is_archived,
  ),
)
const hasConversation = computed(() => Boolean(currentConversation.value?.id))
const isArchived = computed(() => Boolean(currentConversation.value?.is_archived))
const canSend = computed(
  () =>
    !sending.value &&
    !uploading.value &&
    !isArchived.value &&
    (composer.value.trim() || attachments.value.length),
)

function notify(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.value = ''
  }, 3200)
}

function setBootError(error) {
  bootError.value = error instanceof Error ? error.message : String(error)
}

async function bootstrap() {
  loading.value = true
  bootError.value = ''
  try {
    const [capabilityPayload, modelPayload] = await Promise.all([
      api.capabilities(),
      api.models().catch(() => ({ items: [] })),
    ])
    skills.value = capabilityPayload.skills || []
    tools.value = capabilityPayload.tools || []
    models.value = modelPayload.items || []
    await loadConversations()
    if (conversations.value.length && !currentConversation.value) {
      const savedId = window.localStorage.getItem('video-fission.activeConversation')
      const savedExists = conversations.value.some((item) => item.id === savedId)
      await selectConversation(
        (savedExists ? savedId : null) ||
          conversations.value.find((item) => !item.is_archived)?.id ||
          conversations.value[0].id,
      )
    }
  } catch (error) {
    setBootError(error)
  } finally {
    loading.value = false
  }
}

async function loadCapabilities() {
  const payload = await api.capabilities()
  skills.value = payload.skills || []
  tools.value = payload.tools || []
}

async function loadConversations() {
  listLoading.value = true
  try {
    const payload = await api.conversations(true)
    conversations.value = payload.items || []
  } finally {
    listLoading.value = false
  }
}

function closeStream() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  streamConnected.value = false
}

function connectStream(conversationId, afterId = 0) {
  closeStream()
  eventSource = new EventSource(eventUrl(conversationId, afterId))
  eventSource.onopen = () => {
    streamConnected.value = true
  }
  eventSource.onerror = () => {
    streamConnected.value = false
  }
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (!isRefreshEvent(data.kind)) return
      scheduleRefresh(conversationId)
    } catch {
      // Ignore malformed stream payloads; EventSource will continue.
    }
  }
}

function scheduleRefresh(conversationId) {
  refreshQueued = true
  if (refreshTimer) return
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    flushScheduledRefresh(conversationId)
  }, 140)
}

async function flushScheduledRefresh(conversationId) {
  if (currentConversation.value?.id !== conversationId) {
    refreshQueued = false
    return
  }
  if (refreshInFlight) return
  refreshInFlight = true
  refreshQueued = false
  try {
    const [detail, list] = await Promise.all([
      api.conversation(conversationId),
      api.conversations(true),
    ])
    if (currentConversation.value?.id !== conversationId) return
    currentConversation.value = detail
    conversations.value = list.items || []
  } catch {
    // Keep the current transcript visible during transient stream reconnects.
  } finally {
    refreshInFlight = false
    if (refreshQueued && currentConversation.value?.id === conversationId) {
      scheduleRefresh(conversationId)
    }
  }
}

async function selectConversation(id) {
  if (!id) return
  const sequence = ++selectionSequence
  closeStream()
  try {
    const detail = await api.conversation(id)
    if (sequence !== selectionSequence) return
    currentConversation.value = detail
    window.localStorage.setItem('video-fission.activeConversation', id)
    titleBeforeEdit = currentConversation.value.title
    uploadGeneration += 1
    uploading.value = false
    attachments.value = []
    skillMentions.value = []
    mobileSidebar.value = false
    connectStream(id, detail.latest_event_id || 0)
    await loadConversations()
    await nextTick()
    timeline.value?.scrollToBottom('instant')
  } catch (error) {
    notify(error.message)
  }
}

async function newConversation() {
  try {
    const model = currentConversation.value?.model || models.value[0]?.id || null
    const effort =
      models.value.find((item) => item.id === model)?.default_reasoning_effort || 'high'
    const created = await api.createConversation({
      model,
      reasoning_effort: effort,
    })
    conversations.value.unshift(created)
    await selectConversation(created.id)
    mobileSidebar.value = false
    await nextTick()
    composerInput.value?.focus()
  } catch (error) {
    notify(error.message)
  }
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  window.localStorage.setItem(
    'video-fission.sidebarCollapsed',
    String(sidebarCollapsed.value),
  )
}

function startSidebarResize(event) {
  if (window.innerWidth <= 900) return
  event.preventDefault()
  sidebarResizeStartX = event.clientX
  sidebarResizeStartWidth = sidebarWidth.value
  document.body.classList.add('is-resizing-sidebar')
  window.addEventListener('pointermove', resizeSidebar)
  window.addEventListener('pointerup', stopSidebarResize, { once: true })
  window.addEventListener('pointercancel', stopSidebarResize, { once: true })
}

function resizeSidebar(event) {
  sidebarWidth.value = clampSidebarWidth(
    sidebarResizeStartWidth + event.clientX - sidebarResizeStartX,
  )
}

function stopSidebarResize() {
  document.body.classList.remove('is-resizing-sidebar')
  window.removeEventListener('pointermove', resizeSidebar)
  window.removeEventListener('pointerup', stopSidebarResize)
  window.removeEventListener('pointercancel', stopSidebarResize)
  window.localStorage.setItem('video-fission.sidebarWidth', String(sidebarWidth.value))
}

function handleSidebarResizeKey(event) {
  const step = event.shiftKey ? 24 : 8
  let next = sidebarWidth.value
  if (event.key === 'ArrowLeft') next -= step
  else if (event.key === 'ArrowRight') next += step
  else if (event.key === 'Home') next = SIDEBAR_MIN_WIDTH
  else if (event.key === 'End') next = SIDEBAR_MAX_WIDTH
  else return
  event.preventDefault()
  sidebarWidth.value = clampSidebarWidth(next)
  window.localStorage.setItem('video-fission.sidebarWidth', String(sidebarWidth.value))
}

function resetSidebarWidth() {
  sidebarWidth.value = SIDEBAR_DEFAULT_WIDTH
  window.localStorage.setItem('video-fission.sidebarWidth', String(SIDEBAR_DEFAULT_WIDTH))
}

async function refreshCurrent() {
  if (!currentConversation.value?.id) return
  const id = currentConversation.value.id
  try {
    const detail = await api.conversation(id)
    if (currentConversation.value?.id !== id) return
    currentConversation.value = detail
    await loadConversations()
  } catch (error) {
    notify(error.message)
  }
}

async function saveTitle() {
  const title = currentConversation.value?.title?.trim()
  if (!title || title === titleBeforeEdit) return
  const conversationId = currentConversation.value.id
  try {
    const updated = await api.updateConversation(conversationId, { title })
    if (currentConversation.value?.id !== conversationId) return
    currentConversation.value = { ...currentConversation.value, ...updated }
    titleBeforeEdit = updated.title
    await loadConversations()
  } catch (error) {
    notify(error.message)
  }
}

async function updateModel(modelId = currentConversation.value?.model) {
  if (!currentConversation.value?.id) return
  currentConversation.value.model = modelId
  const model = models.value.find((item) => item.id === modelId)
  const effort =
    model?.default_reasoning_effort ||
    currentConversation.value.reasoning_effort ||
    'high'
  currentConversation.value.reasoning_effort = effort
  try {
    await api.updateConversation(currentConversation.value.id, {
      model: currentConversation.value.model,
      reasoning_effort: effort,
    })
    notify('下一条消息起使用新模型')
  } catch (error) {
    notify(error.message)
  }
}

async function updateEffort(effort) {
  if (!currentConversation.value?.id) return
  currentConversation.value.reasoning_effort = effort
  try {
    await api.updateConversation(currentConversation.value.id, {
      reasoning_effort: effort,
    })
  } catch (error) {
    notify(error.message)
  }
}

async function uploadFiles(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length) return
  const generation = ++uploadGeneration
  uploading.value = true
  try {
    for (const file of files) {
      const uploaded = await api.upload(file)
      if (generation !== uploadGeneration) return
      attachments.value.push(uploaded)
    }
    notify(`已添加 ${files.length} 个文件`)
  } catch (error) {
    notify(error.message)
  } finally {
    if (generation === uploadGeneration) uploading.value = false
  }
}

async function removeAttachment(id) {
  try {
    await api.deleteUpload(id)
    attachments.value = attachments.value.filter((file) => file.id !== id)
  } catch (error) {
    notify(error.message)
  }
}

function useSkill(skill) {
  if (!skillMentions.value.some((item) => item.path === `skill://${skill.slug}`)) {
    skillMentions.value.push({
      name: skill.name,
      path: `skill://${skill.slug}`,
    })
  }
  mobileCapabilities.value = false
  composerInput.value?.focus()
}

function removeMention(path) {
  skillMentions.value = skillMentions.value.filter((item) => item.path !== path)
}

async function send() {
  if (!canSend.value) return
  if (isArchived.value) {
    notify('已归档会话不能继续发送消息')
    return
  }
  sending.value = true
  const text = composer.value.trim()
  const files = [...attachments.value]
  const mentions = [...skillMentions.value]
  try {
    let conversation = currentConversation.value
    if (!conversation?.id) {
      await newConversation()
      conversation = currentConversation.value
    }
    if (!conversation?.id) return
    const payload = { text, files, mentions }
    if (conversation.status === 'running') {
      await api.steer(conversation.id, payload)
    } else {
      await api.sendMessage(conversation.id, payload)
    }
    composer.value = ''
    attachments.value = []
    skillMentions.value = []
    await refreshCurrent()
    timeline.value?.scrollToBottom()
  } catch (error) {
    notify(error.message)
  } finally {
    sending.value = false
    composerInput.value?.focus()
  }
}

async function stopTurn() {
  if (!currentConversation.value?.id) return
  try {
    await api.interrupt(currentConversation.value.id)
    await refreshCurrent()
    notify('已停止当前执行')
  } catch (error) {
    notify(error.message)
  }
}

async function decideApproval(id, decision) {
  try {
    await api.decideApproval(id, decision)
    await refreshCurrent()
  } catch (error) {
    notify(error.message)
  }
}

async function answerClarification({ id, answers }) {
  try {
    await api.answerClarification(id, answers)
    await refreshCurrent()
  } catch (error) {
    notify(error.message)
  }
}

async function archiveConversation(id) {
  try {
    await api.archive(id)
    if (id === currentConversation.value?.id) {
      currentConversation.value = {
        ...currentConversation.value,
        is_archived: true,
        archived_at: new Date().toISOString(),
      }
    }
    await loadConversations()
    notify('对话已归档')
  } catch (error) {
    notify(error.message)
  }
}

async function unarchiveConversation(id) {
  try {
    await api.unarchive(id)
    if (id === currentConversation.value?.id) {
      currentConversation.value = {
        ...currentConversation.value,
        is_archived: false,
        archived_at: null,
      }
    }
    await loadConversations()
    notify('已取消归档')
  } catch (error) {
    notify(error.message)
  }
}

async function forkConversation() {
  if (!currentConversation.value?.id) return
  try {
    const forked = await api.fork(currentConversation.value.id)
    await loadConversations()
    await selectConversation(forked.id)
    notify('已从当前上下文创建分叉')
  } catch (error) {
    notify(error.message)
  }
}

async function compactConversation() {
  if (!currentConversation.value?.id) return
  try {
    await api.compact(currentConversation.value.id)
    notify('已开始压缩上下文')
  } catch (error) {
    notify(error.message)
  }
}

async function syncConversation() {
  if (!currentConversation.value?.id) return
  try {
    const result = await api.sync(currentConversation.value.id)
    await refreshCurrent()
    notify(`已同步 ${result.synced} 条运行时记录`)
  } catch (error) {
    notify(error.message)
  }
}

async function deleteConversation() {
  if (!currentConversation.value?.id) return
  if (!window.confirm('永久删除这个对话及其本地记录？')) return
  try {
    await api.deleteConversation(currentConversation.value.id)
    window.localStorage.removeItem('video-fission.activeConversation')
    closeStream()
    currentConversation.value = null
    await loadConversations()
    const next = conversations.value.find((item) => !item.is_archived)
    if (next) await selectConversation(next.id)
    notify('对话已删除')
  } catch (error) {
    notify(error.message)
  }
}

async function toggleSkill(slug, enabled) {
  try {
    await api.toggleSkill(slug, enabled)
    await loadCapabilities()
    notify(enabled ? 'Skill 已启用' : 'Skill 已停用')
  } catch (error) {
    notify(error.message)
  }
}

function openSkillEditor(skill = null) {
  editingSkill.value = skill
  skillModalOpen.value = true
}

async function saveSkill(payload) {
  skillBusy.value = true
  try {
    if (editingSkill.value) {
      await api.updateSkill(editingSkill.value.slug, payload)
    } else {
      await api.createSkill(payload)
    }
    skillModalOpen.value = false
    await loadCapabilities()
    notify(editingSkill.value ? 'Skill 已更新' : 'Skill 已创建')
  } catch (error) {
    notify(error.message)
  } finally {
    skillBusy.value = false
  }
}

async function deleteSkill(skill) {
  if (!window.confirm(`删除 Skill「${skill.name}」？`)) return
  try {
    await api.deleteSkill(skill.slug)
    await loadCapabilities()
    notify('Skill 已删除')
  } catch (error) {
    notify(error.message)
  }
}

async function copyReply(text) {
  try {
    await navigator.clipboard.writeText(text)
    notify('已复制回复')
  } catch {
    notify('复制失败，请手动选择文本')
  }
}

function usePrompt(text) {
  composer.value = text
  nextTick(() => composerInput.value?.focus())
}

function insertLineBreak(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

function handleShortcut(event) {
  if (event.key === 'Escape') {
    mobileSidebar.value = false
    mobileCapabilities.value = false
    skillModalOpen.value = false
    return
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'n') {
    event.preventDefault()
    newConversation()
  }
}

watch(showArchived, async () => {
  await loadConversations()
})

onMounted(() => {
  bootstrap()
  window.addEventListener('keydown', handleShortcut)
})

onBeforeUnmount(() => {
  closeStream()
  clearTimeout(refreshTimer)
  refreshQueued = false
  clearTimeout(toastTimer)
  stopSidebarResize()
  window.removeEventListener('keydown', handleShortcut)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'sidebar-collapsed': sidebarCollapsed,
      'mode-chat': isChatMode,
      'mode-studio': workspaceMode === 'studio',
      'mode-settings': workspaceMode === 'settings',
    }"
    :style="{ '--sidebar-width': isChatMode ? `${sidebarGridWidth}px` : '300px' }"
  >
    <ConversationSidebar
      v-if="isChatMode"
      :conversations="visibleConversations"
      :current-id="currentConversation?.id || ''"
      :show-archived="showArchived"
      :loading="loading || listLoading"
      :mobile-open="mobileSidebar"
      :collapsed="sidebarCollapsed"
      :width="sidebarWidth"
      @new="newConversation"
      @select="selectConversation"
      @archive="archiveConversation"
      @unarchive="unarchiveConversation"
      @toggle-archived="showArchived = !showArchived"
      @close-mobile="mobileSidebar = false"
      @toggle-collapse="toggleSidebar"
      @resize-start="startSidebarResize"
      @resize-keydown="handleSidebarResizeKey"
      @resize-reset="resetSidebarWidth"
    />

    <aside
      v-else
      class="sidebar workspace-rail"
      :class="{ 'is-settings-rail': workspaceMode === 'settings' }"
    >
      <header class="brand-row">
        <BrandLockup />
      </header>
      <div v-if="workspaceMode === 'studio'" id="studio-history-rail" class="rail-slot"></div>
    </aside>

    <main class="workspace" :class="{ 'page-mode': !isChatMode }">
      <header class="topbar">
        <button
          v-if="isChatMode"
          class="icon-button mobile-only sidebar-toggle"
          aria-label="打开会话列表"
          @click="mobileSidebar = true"
        >
          <Menu :size="19" />
        </button>
        <div class="conversation-heading">
          <template v-if="isChatMode">
            <input
              v-if="currentConversation"
              v-model="currentConversation.title"
              class="title-input"
              aria-label="对话标题"
              maxlength="120"
              @focus="titleBeforeEdit = currentConversation.title"
              @blur="saveTitle"
              @keydown.enter.target.blur
            />
            <h1 v-else>Video Fission</h1>
            <div class="conversation-meta">
              <span v-if="streamConnected" class="live-label"><i></i> LIVE</span>
              <span v-else-if="currentConversation" class="live-label muted">历史已保存</span>
              <span v-if="currentConversation?.status === 'running'">
                <Activity :size="12" /> 执行中
              </span>
            </div>
          </template>
          <template v-else>
            <h1>{{ workspaceMode === 'studio' ? '视频创作' : '模型设置' }}</h1>
            <div class="conversation-meta">
              <span class="page-subtitle">{{ workspaceMode === 'studio' ? '本地管线可零配置出片' : '密钥只写不回显' }}</span>
            </div>
          </template>
        </div>

        <div class="topbar-controls">
          <WorkspaceModeNav v-model="workspaceMode" />
          <TopbarSelect
            v-if="isChatMode && currentConversation"
            v-model="currentConversation.model"
            class="model-control"
            caption="模型"
            aria-label="模型"
            :options="modelOptions"
            @change="updateModel"
          >
            <template #icon><span class="control-prefix">M</span></template>
          </TopbarSelect>
          <TopbarSelect
            v-if="isChatMode && currentConversation"
            v-model="currentConversation.reasoning_effort"
            class="effort-control"
            caption="推理"
            aria-label="推理强度"
            :options="effortOptions"
            @change="updateEffort"
          >
            <template #icon>
              <span class="control-prefix"><Zap :size="13" /></span>
            </template>
          </TopbarSelect>

          <details v-if="isChatMode && currentConversation" class="more-menu">
            <summary class="icon-button" aria-label="更多操作"><MoreHorizontal :size="19" /></summary>
            <div class="menu-popover">
              <button @click="syncConversation"><RefreshCw :size="14" /> 同步历史</button>
              <button @click="forkConversation"><GitBranch :size="14" /> 创建分叉</button>
              <button @click="compactConversation"><Settings2 :size="14" /> 压缩上下文</button>
              <button
                v-if="!currentConversation.is_archived"
                @click="archiveConversation(currentConversation.id)"
              >
                <Archive :size="14" /> 归档
              </button>
              <button class="danger" @click="deleteConversation">
                <Trash2 :size="14" /> 永久删除
              </button>
            </div>
          </details>
          <button
            v-if="isChatMode"
            class="icon-button mobile-only capability-toggle"
            aria-label="打开能力台"
            @click="mobileCapabilities = true"
          >
            <PanelRight :size="19" />
          </button>
        </div>
      </header>

      <template v-if="isChatMode">
      <div v-if="bootError" class="boot-error">
        <strong>无法连接 Video Fission</strong>
        <span>{{ bootError }}</span>
        <button class="button subtle" @click="bootstrap">重试</button>
      </div>

      <div v-else-if="loading" class="loading-stage">
        <span class="loading-mark"></span>
        <p>正在准备创作工作台</p>
      </div>

      <MessageTimeline
        v-else
        ref="timeline"
        :conversation="currentConversation"
        @decide-approval="decideApproval"
        @answer-clarification="answerClarification"
        @copy="copyReply"
      />

      <section class="composer-dock">
        <div v-if="!hasConversation" class="prompt-rail">
          <button
            v-for="prompt in [
              ['把一个产品卖点拆成 30 秒短视频', '卖点拆解'],
              ['为美妆新品写一个反常识开场', '开场钩子'],
              ['设计一套三段式种草脚本', '脚本结构'],
            ]"
            :key="prompt[1]"
            data-testid="quick-prompt"
            @click="usePrompt(prompt[0])"
          >
            <span>{{ prompt[1] }}</span>
            {{ prompt[0] }}
            <ArrowUp :size="13" />
          </button>
        </div>

        <div class="composer">
          <div v-if="skillMentions.length || attachments.length" class="composer-context">
            <span
              v-for="mention in skillMentions"
              :key="mention.path"
              class="context-chip skill-chip"
            >
              @{{ mention.name }}
              <button aria-label="移除 Skill" @click="removeMention(mention.path)">
                <X :size="11" />
              </button>
            </span>
            <span v-for="file in attachments" :key="file.id" class="context-chip">
              {{ file.name }}
              <button :aria-label="`移除 ${file.name}`" @click="removeAttachment(file.id)">
                <X :size="11" />
              </button>
            </span>
          </div>
          <textarea
            ref="composerInput"
            v-model="composer"
            data-testid="message-input"
            rows="1"
            maxlength="30000"
            :disabled="isArchived"
            :placeholder="
              isArchived
                ? '该对话已归档，取消归档后可继续发送'
                : currentConversation?.status === 'running'
                ? '追问当前执行，或补充新的限制…'
                : '描述你想拆解、策划或完成的视频任务…'
            "
            @keydown="insertLineBreak"
          ></textarea>
          <div class="composer-footer">
            <div class="composer-tools">
              <label class="icon-button file-button" title="添加文件">
                <Paperclip :size="18" />
                <input
                  type="file"
                  multiple
                  hidden
                  :disabled="uploading || isArchived"
                  @change="uploadFiles"
                />
              </label>
              <button
                class="capability-trigger"
                type="button"
                @click="mobileCapabilities = true"
              >
                <Zap :size="13" />
                {{ skills.length + tools.length }} 项能力
              </button>
              <span v-if="uploading" class="uploading-label">上传中…</span>
            </div>
            <div class="send-actions">
              <button
                v-if="currentConversation?.status === 'running'"
                class="stop-button"
                data-testid="stop-run"
                title="停止执行"
                @click="stopTurn"
              >
                <Square :size="15" fill="currentColor" />
              </button>
              <button
                class="send-button"
                :disabled="!canSend"
                data-testid="send-message"
                :title="currentConversation?.status === 'running' ? '追问' : '发送'"
                @click="send"
              >
                <ArrowUp :size="19" />
              </button>
            </div>
          </div>
        </div>
        <p class="composer-hint">
          <span>ENTER 发送 · SHIFT + ENTER 换行</span>
          <span>受控执行 · 工具输出进入会话记录</span>
        </p>
      </section>
      </template>

      <VideoStudio v-else-if="workspaceMode === 'studio'" @notify="notify" />
      <ModelSettings v-else-if="workspaceMode === 'settings'" @notify="notify" />
    </main>

    <CapabilityPanel
      v-if="isChatMode"
      :skills="skills"
      :tools="tools"
      :mobile-open="mobileCapabilities"
      @create-skill="openSkillEditor()"
      @edit-skill="openSkillEditor"
      @delete-skill="deleteSkill"
      @toggle-skill="toggleSkill"
      @use-skill="useSkill"
      @close-mobile="mobileCapabilities = false"
    />

    <SkillEditorModal
      :open="skillModalOpen"
      :skill="editingSkill"
      :busy="skillBusy"
      @close="skillModalOpen = false"
      @save="saveSkill"
    />

    <Transition name="toast">
      <div v-if="toast" class="toast-message" role="status">
        <Check :size="15" />
        {{ toast }}
      </div>
    </Transition>
  </div>
</template>

