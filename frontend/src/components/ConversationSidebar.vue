<script setup>
import {
  Archive,
  ArchiveRestore,
  ChevronRight,
  Clapperboard,
  LoaderCircle,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings2,
  X,
} from '@lucide/vue'
import { computed, ref } from 'vue'
import { compactTitle, formatRelativeTime } from '../utils/events'
import BrandLockup from './BrandLockup.vue'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  currentId: { type: String, default: '' },
  showArchived: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  mobileOpen: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  width: { type: Number, default: 278 },
  productionBadges: { type: Object, default: () => ({}) },
  unboundProductions: { type: Array, default: () => [] },
  settingsActive: { type: Boolean, default: false },
})

const emit = defineEmits([
  'new',
  'select',
  'archive',
  'unarchive',
  'toggle-archived',
  'close-mobile',
  'toggle-collapse',
  'resize-start',
  'resize-keydown',
  'resize-reset',
  'bind-unbound',
  'open-settings',
])

const query = ref('')
const unboundOpen = ref(true)
const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return props.conversations
  return props.conversations.filter((item) =>
    `${item.title} ${item.last_error || ''}`.toLowerCase().includes(needle),
  )
})
</script>

<template>
  <aside
    id="conversation-sidebar"
    class="sidebar"
    :class="{ 'is-mobile-open': mobileOpen, 'is-collapsed': collapsed }"
  >
    <header class="brand-row">
      <BrandLockup />
      <button
        class="icon-button sidebar-collapse-button"
        :aria-label="collapsed ? '展开会话侧栏' : '折叠会话侧栏'"
        :title="collapsed ? '展开侧栏' : '折叠侧栏'"
        @click="$emit('toggle-collapse')"
      >
        <PanelLeftOpen v-if="collapsed" :size="17" />
        <PanelLeftClose v-else :size="17" />
      </button>
      <button class="icon-button sidebar-close" aria-label="关闭会话列表" @click="$emit('close-mobile')">
        <X :size="18" />
      </button>
    </header>

    <button
      class="new-chat-button"
      data-testid="new-conversation"
      aria-label="新建对话"
      title="新建对话"
      @click="$emit('new')"
    >
      <Plus :size="17" />
      <span>新建对话</span>
      <kbd>⌘ N</kbd>
    </button>

    <label class="conversation-search">
      <Search :size="15" />
      <input v-model="query" type="search" placeholder="搜索对话" aria-label="搜索对话" />
    </label>

    <div class="sidebar-section-label">
      <span>{{ showArchived ? '归档对话' : '最近对话' }}</span>
      <LoaderCircle v-if="loading" class="spin" :size="14" />
      <span v-else>{{ filtered.length }}</span>
    </div>

    <nav class="conversation-list" aria-label="对话列表">
      <button
        v-for="conversation in filtered"
        :key="conversation.id"
        class="conversation-row"
        :class="{ active: conversation.id === currentId }"
        data-testid="conversation-item"
        @click="$emit('select', conversation.id)"
      >
        <span class="conversation-icon">
          <MessageSquare :size="15" />
          <i v-if="conversation.is_running" class="running-pip"></i>
        </span>
        <span class="conversation-copy">
          <strong>{{ compactTitle(conversation.title) }}</strong>
          <small>
            {{ formatRelativeTime(conversation.updated_at) }}
            <template v-if="conversation.last_error"> · 有错误</template>
          </small>
          <em
            v-if="productionBadges[conversation.id]"
            class="conversation-production-badge"
            data-testid="conversation-production-badge"
          >{{ productionBadges[conversation.id] }}</em>
        </span>
        <span
          class="row-action"
          :title="conversation.is_archived ? '取消归档' : '归档'"
          role="button"
          tabindex="0"
          @click.stop="
            $emit(conversation.is_archived ? 'unarchive' : 'archive', conversation.id)
          "
          @keydown.enter.stop="
            $emit(conversation.is_archived ? 'unarchive' : 'archive', conversation.id)
          "
        >
          <ArchiveRestore v-if="conversation.is_archived" :size="14" />
          <Archive v-else :size="14" />
        </span>
      </button>

      <div v-if="!filtered.length && !loading" class="sidebar-empty">
        <p>{{ query ? '没有匹配的对话' : '还没有对话' }}</p>
        <button v-if="!query" @click="$emit('new')">开始第一次对话</button>
      </div>
    </nav>

    <section v-if="unboundProductions.length" class="unbound-productions" data-testid="unbound-productions">
      <button
        class="sidebar-section-label unbound-toggle"
        type="button"
        :aria-expanded="unboundOpen"
        @click="unboundOpen = !unboundOpen"
      >
        <span>
          <ChevronRight :size="14" :class="{ open: unboundOpen }" />
          未归入对话的成片
        </span>
        <span>{{ unboundProductions.length }}</span>
      </button>
      <nav v-if="unboundOpen" class="conversation-list unbound-list" aria-label="未归入对话的成片">
        <button
          v-for="item in unboundProductions"
          :key="item.id"
          class="conversation-row"
          type="button"
          :data-testid="`unbound-production-${item.id}`"
          @click="$emit('bind-unbound', item.id)"
        >
          <span class="conversation-icon">
            <Clapperboard :size="15" />
          </span>
          <span class="conversation-copy">
            <strong>{{ compactTitle(item.title || item.brief || item.id) }}</strong>
            <small>{{ item.badge || item.id }}</small>
          </span>
        </button>
      </nav>
    </section>

    <footer class="sidebar-footer">
      <button class="archive-toggle" @click="$emit('toggle-archived')">
        <Archive :size="14" />
        {{ showArchived ? '返回最近对话' : '查看归档' }}
      </button>
      <button
        class="sidebar-settings-button"
        type="button"
        data-testid="mode-settings"
        :class="{ active: settingsActive }"
        :aria-pressed="settingsActive"
        aria-label="系统设置"
        title="系统设置"
        @click="$emit('open-settings')"
      >
        <Settings2 :size="15" />
        <span>系统设置</span>
      </button>
    </footer>

    <div
      class="sidebar-resize-handle"
      role="separator"
      aria-label="调整会话侧栏宽度"
      aria-orientation="vertical"
      :aria-valuemin="240"
      :aria-valuemax="420"
      :aria-valuenow="width"
      tabindex="0"
      title="拖拽调整宽度，双击恢复默认"
      @pointerdown="$emit('resize-start', $event)"
      @keydown="$emit('resize-keydown', $event)"
      @dblclick="$emit('resize-reset')"
    ></div>
  </aside>
</template>

