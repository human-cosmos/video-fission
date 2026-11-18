<script setup>
import {
  Activity,
  ArrowUp,
  Check,
  Clapperboard,
  Menu,
  Paperclip,
  SlidersHorizontal,
  Square,
  X,
  Zap,
} from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, eventUrl } from './api'
import ConversationSidebar from './components/ConversationSidebar.vue'
import MessageTimeline from './components/MessageTimeline.vue'
import ModelSettings from './components/ModelSettings.vue'
import TopbarSelect from './components/TopbarSelect.vue'
import {
  isConversationListEvent,
  isProductionRefreshEvent,
  isRefreshEvent,
  mergeConversationSummaries,
} from './utils/events'
import {
  MAX_PRODUCTION_UPLOAD_BYTES,
  POLICY_MODES,
  VIDEO_ACCEPT,
  WORKSPACE_MODES,
  formatDuration,
  isVideoFile,
  toProductionVideoFile,
} from './utils/format'

defineProps({
  currentUser: { type: Object, required: true },
})
defineEmits(['logout'])

const conversations = ref([])
const currentConversation = ref(null)
const models = ref([])
const loading = ref(true)
const listLoading = ref(false)
const bootError = ref('')
const composer = ref('')
const attachments = ref([])
const uploading = ref(false)
const sending = ref(false)
const policyMode = ref('brief')
const productions = ref([])
const rerunning = ref(false)
const selectingScript = ref(false)
const cancelling = ref(false)
const videoInput = ref(null)
const showArchived = ref(false)
const conversationQuery = ref('')
const nextConversationCursor = ref('')
const hasMoreConversations = ref(false)
const loadingMoreConversations = ref(false)
const mobileSidebar = ref(false)
const mobileSessionOpen = ref(false)
const toast = ref('')
const streamConnected = ref(false)
const timeline = ref(null)
const composerInput = ref(null)
let eventSource = null
let transcriptRefreshTimer = null
let transcriptRefreshInFlight = false
let transcriptRefreshQueued = false
let listRefreshTimer = null
let listRefreshInFlight = false
let listRefreshQueued = false
let productionRefreshTimer = null
let productionRefreshInFlight = false
let productionRefreshQueued = false
let conversationListSequence = 0
let conversationSearchTimer = null
let conversationPollTimer = null
let selectionSequence = 0
let uploadGeneration = 0
let toastTimer = null
let titleBeforeEdit = ''
let bootstrapPromise = null
let navigationPromise = null

const MODE_STORAGE_KEY = 'video-fission.workspaceMode'
const MODE_IDS = WORKSPACE_MODES.map((item) => item.id)
const CONVERSATION_PAGE_SIZE = 30
const CONVERSATION_POLL_MS = 15_000

function storedWorkspaceMode() {
  const hashMode = window.location.hash.replace(/^#/, '')
  if (MODE_IDS.includes(hashMode)) return hashMode
  const stored = window.localStorage.getItem(MODE_STORAGE_KEY)
  return MODE_IDS.includes(stored) ? stored : 'chat'
}

const workspaceMode = ref(storedWorkspaceMode())
const isChatMode = computed(() => workspaceMode.value === 'chat')

watch(workspaceMode, (mode) => {
  window.localStorage.setItem(MODE_STORAGE_KEY, mode)
  const baseUrl = `${window.location.pathname}${window.location.search}`
  window.history.replaceState(
    {},
    '',
    mode === 'settings' ? `${baseUrl}#settings` : baseUrl,
  )
  if (mode !== 'chat') {
    mobileSidebar.value = false
    mobileSessionOpen.value = false
  }
})

let sidebarResizeStartX = 0
let sidebarResizeStartWidth = 0

const SIDEBAR_MIN_WIDTH = 232
const SIDEBAR_MAX_WIDTH = 420
const SIDEBAR_DEFAULT_WIDTH = 260
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
const productionVideo = computed(() => attachments.value.find((file) => isVideoFile(file)))
const otherAttachments = computed(() => attachments.value.filter((file) => !isVideoFile(file)))
const needsBriefHint = computed(() => Boolean(productionVideo.value) && !composer.value.trim())
const awaitingScriptChoice = computed(() =>
  (currentConversation.value?.items || []).some(
    (item) => item?.item_type === 'video_progress' && item?.payload?.status === 'awaiting_script_choice',
  ),
)
const canSend = computed(
  () =>
    !sending.value &&
    !uploading.value &&
    !isArchived.value &&
    (productionVideo.value ? Boolean(composer.value.trim()) : Boolean(composer.value.trim() || attachments.value.length)),
)
const unboundProductions = computed(() =>
  (productions.value || [])
    .filter((item) => !item.conversation_id)
    .map((item) => ({
      ...item,
      badge:
        item.status === 'completed' && item.output?.duration_ms
          ? formatDuration(item.output.duration_ms)
          : item.status === 'completed'
            ? '成片'
            : item.title || item.brief || item.id,
    })),
)
const productionBadges = computed(() => {
  const badges = {}
  for (const item of productions.value || []) {
    if (!item.conversation_id) continue
    if (badges[item.conversation_id] && item.status !== 'completed') continue
    badges[item.conversation_id] =
      item.status === 'completed' && (item.output?.duration_ms || item.duration_ms)
        ? formatDuration(item.output?.duration_ms || item.duration_ms)
        : '成片'
  }
  return badges
})

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
  if (bootstrapPromise) return bootstrapPromise
  bootstrapPromise = runBootstrap()
  try {
    return await bootstrapPromise
  } finally {
    bootstrapPromise = null
  }
}

async function waitForAppReady() {
  while (bootstrapPromise || navigationPromise) {
    await Promise.all([bootstrapPromise, navigationPromise].filter(Boolean))
  }
}

async function runBootstrap() {
  loading.value = true
  bootError.value = ''
  try {
    const modelPayload = await api.models().catch(() => ({ items: [] }))
    models.value = modelPayload.items || []
    await Promise.all([
      loadConversations({ reset: true }),
      loadProductions(),
    ])
    if (
      workspaceMode.value === 'chat' &&
      visibleConversations.value.length &&
      !currentConversation.value
    ) {
      const savedId = window.localStorage.getItem('video-fission.activeConversation')
      const savedExists = conversations.value.some((item) => item.id === savedId)
      await selectConversation(
        (savedExists ? savedId : null) ||
          conversations.value.find((item) => !item.is_archived)?.id ||
          conversations.value[0].id,
      )
    } else if (workspaceMode.value === 'chat' && !visibleConversations.value.length) {
      const archived = await api.conversations({ archived: true, limit: 1 })
      if (archived.items?.length) {
        showArchived.value = true
        await loadConversations({ reset: true })
        await selectConversation(visibleConversations.value[0].id)
      }
    }
  } catch (error) {
    setBootError(error)
  } finally {
    loading.value = false
  }
}

function appendConversationPage(existing, incoming) {
  const seen = new Set(existing.map((item) => item.id))
  return [...existing, ...incoming.filter((item) => !seen.has(item.id))]
}

async function loadConversations({
  reset = false,
  merge = false,
  silent = false,
} = {}) {
  const cursor = reset || merge ? '' : nextConversationCursor.value
  if (!reset && !merge && !cursor) return
  const sequence = ++conversationListSequence
  if (!silent) {
    if (reset) listLoading.value = true
    else loadingMoreConversations.value = true
  }
  try {
    const payload = await api.conversations({
      archived: showArchived.value,
      cursor,
      query: conversationQuery.value,
      limit: CONVERSATION_PAGE_SIZE,
    })
    if (sequence !== conversationListSequence) return
    const items = payload.items || []
    if (reset) {
      conversations.value = items
    } else if (merge) {
      conversations.value = mergeConversationSummaries(conversations.value, items)
    } else {
      conversations.value = appendConversationPage(conversations.value, items)
    }
    if (reset || (merge && conversations.value.length <= CONVERSATION_PAGE_SIZE) || cursor) {
      nextConversationCursor.value = payload.next_cursor || ''
      hasMoreConversations.value = Boolean(payload.next_cursor)
    }
  } finally {
    if (sequence === conversationListSequence) {
      if (!silent) {
        listLoading.value = false
        loadingMoreConversations.value = false
      }
    }
  }
}

async function loadProductions() {
  try {
    const payload = await api.productions()
    productions.value = payload.items || payload || []
  } catch {
    // History badges are additive; a failed list fetch should not break chat.
  }
}

function revokePreview(file) {
  if (file?.previewUrl) URL.revokeObjectURL(file.previewUrl)
}

function clearAttachments() {
  for (const file of attachments.value) revokePreview(file)
  attachments.value = []
  policyMode.value = 'brief'
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
      const kind = data.kind || data.type
      if (isRefreshEvent(kind)) scheduleTranscriptRefresh(conversationId)
      if (isConversationListEvent(kind)) scheduleListRefresh()
      if (isProductionRefreshEvent(kind)) scheduleProductionRefresh()
    } catch {
      // Ignore malformed stream payloads; EventSource will continue.
    }
  }
}

function scheduleTranscriptRefresh(conversationId) {
  transcriptRefreshQueued = true
  if (transcriptRefreshTimer) return
  transcriptRefreshTimer = setTimeout(() => {
    transcriptRefreshTimer = null
    flushScheduledTranscriptRefresh(conversationId)
  }, 140)
}

async function flushScheduledTranscriptRefresh(conversationId) {
  if (currentConversation.value?.id !== conversationId) {
    transcriptRefreshQueued = false
    return
  }
  if (transcriptRefreshInFlight) return
  transcriptRefreshInFlight = true
  transcriptRefreshQueued = false
  try {
    const detail = await api.conversation(conversationId)
    if (currentConversation.value?.id !== conversationId) return
    currentConversation.value = detail
  } catch {
    // Keep the current transcript visible during transient stream reconnects.
  } finally {
    transcriptRefreshInFlight = false
    if (transcriptRefreshQueued && currentConversation.value?.id === conversationId) {
      scheduleTranscriptRefresh(conversationId)
    }
  }
}

function scheduleListRefresh() {
  listRefreshQueued = true
  if (listRefreshTimer) return
  listRefreshTimer = setTimeout(() => {
    listRefreshTimer = null
    flushScheduledListRefresh()
  }, 180)
}

async function flushScheduledListRefresh() {
  if (listRefreshInFlight) return
  listRefreshInFlight = true
  listRefreshQueued = false
  try {
    await loadConversations({ merge: true, silent: true })
  } catch {
    // Keep the current list visible while the stream reconnects.
  } finally {
    listRefreshInFlight = false
    if (listRefreshQueued) scheduleListRefresh()
  }
}

function scheduleProductionRefresh() {
  productionRefreshQueued = true
  if (productionRefreshTimer) return
  productionRefreshTimer = setTimeout(() => {
    productionRefreshTimer = null
    flushScheduledProductionRefresh()
  }, 180)
}

async function flushScheduledProductionRefresh() {
  if (productionRefreshInFlight) return
  productionRefreshInFlight = true
  productionRefreshQueued = false
  try {
    await loadProductions()
  } finally {
    productionRefreshInFlight = false
    if (productionRefreshQueued) {
      scheduleProductionRefresh()
    }
  }
}

async function selectConversation(id) {
  if (!id) return
  const operation = selectConversationTask(id)
  navigationPromise = operation
  try {
    return await operation
  } finally {
    if (navigationPromise === operation) navigationPromise = null
  }
}

async function selectConversationTask(id) {
  workspaceMode.value = 'chat'
  mobileSessionOpen.value = false
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
    clearAttachments()
    mobileSidebar.value = false
    connectStream(id, detail.latest_event_id || 0)
    await nextTick()
    timeline.value?.scrollToBottom('instant')
  } catch (error) {
    notify(error.message)
  }
}

function openSettings() {
  workspaceMode.value = 'settings'
  mobileSidebar.value = false
  mobileSessionOpen.value = false
}

function openMobileSession() {
  mobileSidebar.value = false
  mobileSessionOpen.value = true
}

function startDraftConversation() {
  selectionSequence += 1
  workspaceMode.value = 'chat'
  closeStream()
  currentConversation.value = null
  titleBeforeEdit = ''
  window.localStorage.removeItem('video-fission.activeConversation')
  uploadGeneration += 1
  uploading.value = false
  clearAttachments()
  mobileSidebar.value = false
  mobileSessionOpen.value = false
  nextTick(() => composerInput.value?.focus())
}

async function newConversation() {
  await waitForAppReady()
  if (showArchived.value) {
    showArchived.value = false
    conversationQuery.value = ''
    await loadConversations({ reset: true })
  }
  startDraftConversation()
}

async function createConversationTask() {
  workspaceMode.value = 'chat'
  try {
    if (showArchived.value) {
      showArchived.value = false
      conversationQuery.value = ''
    }
    const model = currentConversation.value?.model || models.value[0]?.id || null
    const effort =
      models.value.find((item) => item.id === model)?.default_reasoning_effort || 'high'
    const created = await api.createConversation({
      model,
      reasoning_effort: effort,
    })
    conversations.value.unshift({
      ...created,
      list_state: 'empty',
      has_warnings: false,
    })
    await selectConversation(created.id)
    mobileSidebar.value = false
    await nextTick()
    composerInput.value?.focus()
    return currentConversation.value
  } catch (error) {
    notify(error.message)
    return null
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

function handleConversationSearch(value) {
  conversationQuery.value = value
  clearTimeout(conversationSearchTimer)
  conversationSearchTimer = setTimeout(() => {
    conversationSearchTimer = null
    loadConversations({ reset: true, silent: true }).catch(() => {})
  }, 250)
}

async function loadMoreConversations() {
  try {
    await loadConversations()
  } catch (error) {
    notify(error.message)
  }
}

async function alignCurrentConversationWithView() {
  const currentId = currentConversation.value?.id
  if (currentId && visibleConversations.value.some((item) => item.id === currentId)) {
    return
  }
  const next = visibleConversations.value[0]
  if (next) {
    await selectConversation(next.id)
    return
  }
  startDraftConversation()
}

async function toggleArchivedView() {
  showArchived.value = !showArchived.value
  conversationQuery.value = ''
  await loadConversations({ reset: true })
  await alignCurrentConversationWithView()
}

async function pollConversations() {
  if (document.hidden || bootstrapPromise || navigationPromise) return
  await loadConversations({ merge: true, silent: true }).catch(() => {})
}

function handleVisibilityChange() {
  if (!document.hidden) pollConversations()
}

async function refreshCurrent() {
  if (!currentConversation.value?.id) return
  const id = currentConversation.value.id
  try {
    const detail = await api.conversation(id)
    if (currentConversation.value?.id !== id) return
    currentConversation.value = detail
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
    await loadConversations({ merge: true, silent: true })
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

async function attachProductionVideo(file, generation) {
  if (file.size > MAX_PRODUCTION_UPLOAD_BYTES) {
    throw new Error('视频超过 500MB 上限')
  }
  const previewUrl = URL.createObjectURL(file)
  const uploaded = toProductionVideoFile(await api.uploadProductionVideo(file))
  if (generation !== uploadGeneration) {
    URL.revokeObjectURL(previewUrl)
    return
  }
  const previous = attachments.value.filter((item) => isVideoFile(item))
  for (const item of previous) revokePreview(item)
  uploaded.previewUrl = previewUrl
  attachments.value = [...attachments.value.filter((item) => !isVideoFile(item)), uploaded]
}

async function uploadFiles(event) {
  await waitForAppReady()
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length) return
  const generation = ++uploadGeneration
  uploading.value = true
  try {
    const videos = files.filter((file) => isVideoFile(file))
    const others = files.filter((file) => !isVideoFile(file))
    if (videos.length) {
      await attachProductionVideo(videos[0], generation)
      if (generation !== uploadGeneration) return
    }
    for (const file of others) {
      const uploaded = await api.upload(file)
      if (generation !== uploadGeneration) return
      attachments.value.push(uploaded)
    }
    notify(videos.length ? '已添加参考视频' : `已添加 ${files.length} 个文件`)
  } catch (error) {
    notify(error.message)
  } finally {
    if (generation === uploadGeneration) uploading.value = false
  }
}

async function uploadComposerVideo(event) {
  await uploadFiles(event)
}

async function removeAttachment(id) {
  const target = attachments.value.find((file) => file.id === id)
  if (isVideoFile(target)) {
    revokePreview(target)
    attachments.value = attachments.value.filter((file) => file.id !== id)
    return
  }
  try {
    await api.deleteUpload(id)
    attachments.value = attachments.value.filter((file) => file.id !== id)
  } catch (error) {
    notify(error.message)
  }
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
  try {
    let conversation = currentConversation.value
    if (!conversation?.id) {
      await createConversationTask()
      conversation = currentConversation.value
    }
    if (!conversation?.id) return
    const payload = {
      text,
      files: files.map((file) => {
        const { previewUrl, ...rest } = file
        return isVideoFile(file) ? toProductionVideoFile(rest) : rest
      }),
      mentions: [],
    }
    if (productionVideo.value) {
      payload.replace_policy = { mode: policyMode.value || 'brief' }
    }
    if (conversation.status === 'running') {
      await api.steer(conversation.id, payload)
    } else {
      await api.sendMessage(conversation.id, payload)
    }
    composer.value = ''
    clearAttachments()
    await refreshCurrent()
    await Promise.all([
      loadConversations({ merge: true, silent: true }),
      loadProductions(),
    ])
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

async function cancelProduction(productionId) {
  if (!productionId) return
  cancelling.value = true
  try {
    await api.cancelProduction(productionId)
    await refreshCurrent()
    await loadProductions()
    notify('已取消任务')
  } catch (error) {
    notify(error.message)
  } finally {
    cancelling.value = false
  }
}

async function rerunSelected({ productionId, segmentIds }) {
  if (!productionId || !segmentIds?.length) return
  rerunning.value = true
  try {
    await api.runProduction(productionId, {
      replace_policy: { mode: 'selected', segment_ids: segmentIds },
      resume_from: 'intent',
    })
    await refreshCurrent()
    await loadProductions()
    notify('已按选中片段重新生成并合成')
  } catch (error) {
    notify(error.message)
    await refreshCurrent()
  } finally {
    rerunning.value = false
  }
}

async function rerunBrief(productionId) {
  if (!productionId) return
  rerunning.value = true
  try {
    await api.runProduction(productionId, {
      replace_policy: { mode: 'brief' },
      resume_from: 'intent',
    })
    await refreshCurrent()
    await loadProductions()
    notify('已按最新说明重跑')
  } catch (error) {
    notify(error.message)
    await refreshCurrent()
  } finally {
    rerunning.value = false
  }
}

async function selectScript({ productionId, scriptId }) {
  if (!productionId || !scriptId) return
  selectingScript.value = true
  try {
    await api.selectScript(productionId, scriptId)
    await refreshCurrent()
    await loadProductions()
    notify('已选用脚本，开始生成')
  } catch (error) {
    notify(error.message)
    await refreshCurrent()
  } finally {
    selectingScript.value = false
  }
}

async function regenerateScripts(productionId) {
  if (!productionId) return
  rerunning.value = true
  try {
    await api.runProduction(productionId, { resume_from: 'script' })
    await refreshCurrent()
    await loadProductions()
    notify('正在再出 3 个脚本')
  } catch (error) {
    notify(error.message)
    await refreshCurrent()
  } finally {
    rerunning.value = false
  }
}

async function regenerateVideo(productionId) {
  if (!productionId) return
  rerunning.value = true
  try {
    await api.runProduction(productionId, { resume_from: 'generate' })
    await refreshCurrent()
    await loadProductions()
    notify('正在按已选脚本再生成')
  } catch (error) {
    notify(error.message)
    await refreshCurrent()
  } finally {
    rerunning.value = false
  }
}

async function bindUnboundProduction(productionId) {
  try {
    let conversation = currentConversation.value
    if (!conversation?.id) {
      await createConversationTask()
      conversation = currentConversation.value
    }
    if (!conversation?.id) return
    await api.patchProduction(productionId, { conversation_id: conversation.id })
    await refreshCurrent()
    await loadProductions()
    notify('已将成片归入当前对话')
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
    await loadConversations({ reset: true })
    await alignCurrentConversationWithView()
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
    await loadConversations({ reset: true })
    await alignCurrentConversationWithView()
    notify('已取消归档')
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
    mobileSessionOpen.value = false
    return
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'n') {
    event.preventDefault()
    newConversation()
  }
}

onMounted(() => {
  bootstrap()
  window.addEventListener('keydown', handleShortcut)
  document.addEventListener('visibilitychange', handleVisibilityChange)
  conversationPollTimer = setInterval(pollConversations, CONVERSATION_POLL_MS)
})

onBeforeUnmount(() => {
  closeStream()
  clearTimeout(transcriptRefreshTimer)
  clearTimeout(listRefreshTimer)
  clearTimeout(productionRefreshTimer)
  clearTimeout(conversationSearchTimer)
  clearInterval(conversationPollTimer)
  transcriptRefreshQueued = false
  listRefreshQueued = false
  productionRefreshQueued = false
  clearTimeout(toastTimer)
  stopSidebarResize()
  window.removeEventListener('keydown', handleShortcut)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'sidebar-collapsed': sidebarCollapsed,
      'mode-chat': isChatMode,
      'mode-settings': workspaceMode === 'settings',
    }"
    :style="{ '--sidebar-width': `${sidebarGridWidth}px` }"
  >
    <ConversationSidebar
      :current-user="currentUser"
      :conversations="visibleConversations"
      :current-id="currentConversation?.id || ''"
      :show-archived="showArchived"
      :loading="loading || listLoading"
      :mobile-open="mobileSidebar"
      :collapsed="sidebarCollapsed"
      :width="sidebarWidth"
      :production-badges="productionBadges"
      :unbound-productions="unboundProductions"
      :settings-active="workspaceMode === 'settings'"
      :query="conversationQuery"
      :has-more="hasMoreConversations"
      :loading-more="loadingMoreConversations"
      @new="newConversation"
      @select="selectConversation"
      @archive="archiveConversation"
      @unarchive="unarchiveConversation"
      @toggle-archived="toggleArchivedView"
      @search="handleConversationSearch"
      @load-more="loadMoreConversations"
      @close-mobile="mobileSidebar = false"
      @toggle-collapse="toggleSidebar"
      @resize-start="startSidebarResize"
      @resize-keydown="handleSidebarResizeKey"
      @resize-reset="resetSidebarWidth"
      @bind-unbound="bindUnboundProduction"
      @open-settings="openSettings"
      @logout="$emit('logout')"
    />

    <main class="workspace" :class="{ 'page-mode': !isChatMode }">
      <header class="topbar">
        <button
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
            <h1>系统设置</h1>
            <div class="conversation-meta">
              <span class="page-subtitle">账号、Skill 与 Tool</span>
            </div>
          </template>
        </div>

        <div class="topbar-controls">
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

          <button
            v-if="isChatMode && currentConversation"
            class="icon-button mobile-only mobile-session-toggle"
            aria-label="打开会话参数"
            aria-controls="mobile-session-sheet"
            :aria-expanded="mobileSessionOpen"
            @click="openMobileSession"
          >
            <SlidersHorizontal :size="18" />
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
        :rerunning="rerunning"
        :selecting="selectingScript"
        :cancelling="cancelling"
        @decide-approval="decideApproval"
        @answer-clarification="answerClarification"
        @copy="copyReply"
        @cancel-production="cancelProduction"
        @rerun-selected="rerunSelected"
        @rerun-brief="rerunBrief"
        @select-script="selectScript"
        @regenerate-scripts="regenerateScripts"
        @regenerate-video="regenerateVideo"
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
          <div v-if="productionVideo" class="composer-video" data-testid="composer-video-preview">
            <video
              :src="productionVideo.previewUrl || productionVideo.url"
              controls
              preload="metadata"
            ></video>
            <div class="composer-video-meta">
              <strong>{{ productionVideo.name }}</strong>
              <button type="button" class="icon-button compact" :aria-label="`移除 ${productionVideo.name}`" @click="removeAttachment(productionVideo.id)">
                <X :size="14" />
              </button>
            </div>
          </div>
          <fieldset v-if="productionVideo" class="composer-policy studio-policy" data-testid="composer-policy">
            <legend>创作策略</legend>
            <div class="studio-policy-options">
              <label
                v-for="option in POLICY_MODES"
                :key="option.id"
                :class="{ active: policyMode === option.id }"
              >
                <input
                  v-model="policyMode"
                  type="radio"
                  name="composer-policy"
                  :value="option.id"
                  :data-testid="`composer-policy-${option.id}`"
                />
                <span>{{ option.label }}</span>
              </label>
            </div>
          </fieldset>
          <div v-if="otherAttachments.length" class="composer-context">
            <span v-for="file in otherAttachments" :key="file.id" class="context-chip">
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
                : awaitingScriptChoice
                ? '改文案或卖点可再出 3 个脚本；输入「用第 2 个」也可挑选'
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
              <label class="icon-button file-button" title="上传参考视频">
                <Clapperboard :size="18" />
                <input
                  ref="videoInput"
                  type="file"
                  hidden
                  :accept="VIDEO_ACCEPT"
                  data-testid="composer-video-upload"
                  :disabled="uploading || isArchived"
                  @change="uploadComposerVideo"
                />
              </label>
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
        <p v-if="needsBriefHint" class="composer-hint composer-hint-need-brief" data-testid="composer-hint-need-brief">
          补充创作说明后再发送
        </p>
        <p
          v-else-if="awaitingScriptChoice"
          class="composer-hint"
          data-testid="composer-hint-awaiting-script"
        >
          请先在规划卡选用一个脚本。改说明再发送会再出 3 个脚本；「用第 2 个」「选这个」视为挑选。
        </p>
        <p v-else class="composer-hint">
          <span>ENTER 发送 · SHIFT + ENTER 换行</span>
          <span>受控执行 · 工具输出进入会话记录</span>
        </p>
      </section>
      </template>

      <ModelSettings
        v-else-if="workspaceMode === 'settings'"
        :current-user="currentUser"
        @notify="notify"
      />
    </main>

    <Transition name="session-sheet">
      <div
        v-if="isChatMode && currentConversation && mobileSessionOpen"
        class="mobile-session-backdrop"
        @click.self="mobileSessionOpen = false"
      >
        <section
          class="mobile-session-sheet"
          id="mobile-session-sheet"
          role="dialog"
          aria-modal="true"
          aria-labelledby="mobile-session-title"
        >
          <header>
            <div>
              <span>SESSION</span>
              <h2 id="mobile-session-title">会话参数</h2>
            </div>
            <button
              class="icon-button compact"
              aria-label="关闭会话参数"
              @click="mobileSessionOpen = false"
            >
              <X :size="17" />
            </button>
          </header>
          <p>模型和推理强度从下一条消息起生效。</p>
          <div class="mobile-session-fields">
            <TopbarSelect
              v-model="currentConversation.model"
              caption="模型"
              aria-label="模型"
              :options="modelOptions"
              @change="updateModel"
            >
              <template #icon><span class="control-prefix">M</span></template>
            </TopbarSelect>
            <TopbarSelect
              v-model="currentConversation.reasoning_effort"
              caption="推理强度"
              aria-label="推理强度"
              :options="effortOptions"
              @change="updateEffort"
            >
              <template #icon>
                <span class="control-prefix"><Zap :size="13" /></span>
              </template>
            </TopbarSelect>
          </div>
        </section>
      </div>
    </Transition>

    <Transition name="toast">
      <div v-if="toast" class="toast-message" role="status">
        <Check :size="15" />
        {{ toast }}
      </div>
    </Transition>
  </div>
</template>

