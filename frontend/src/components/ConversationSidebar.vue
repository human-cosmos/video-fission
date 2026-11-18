<script setup>
import {
  Archive,
  ArchiveRestore,
  ChevronRight,
  Clapperboard,
  LoaderCircle,
  LogOut,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings2,
  X,
} from '@lucide/vue'
import { computed, ref } from 'vue'
import {
  compactTitle,
  conversationStateLabel,
  formatRelativeTime,
} from '../utils/events'
import BrandLockup from './BrandLockup.vue'

const props = defineProps({
  currentUser: { type: Object, required: true },
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
  query: { type: String, default: '' },
  hasMore: { type: Boolean, default: false },
  loadingMore: { type: Boolean, default: false },
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
  'logout',
  'search',
  'load-more',
])

const unboundOpen = ref(true)
const accountMenu = ref(null)
const accountName = computed(
  () => props.currentUser.display_name || props.currentUser.username || '用户',
)
const accountInitial = computed(() => accountName.value.slice(0, 1).toUpperCase())
const accountRole = computed(() => (props.currentUser.role === 'admin' ? '管理员' : '成员'))

function closeAccountMenu() {
  if (accountMenu.value) accountMenu.value.open = false
}

function openSettings() {
  closeAccountMenu()
  emit('open-settings')
}

function logout() {
  closeAccountMenu()
  emit('logout')
}
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
      <input
        :value="query"
        type="search"
        placeholder="搜索对话"
        aria-label="搜索对话"
        @input="$emit('search', $event.target.value)"
      />
    </label>

    <div class="sidebar-section-label">
      <span>{{ showArchived ? '归档对话' : '最近对话' }}</span>
      <LoaderCircle v-if="loading" class="spin" :size="14" />
      <span v-else>{{ conversations.length }}</span>
    </div>

    <nav class="conversation-list" aria-label="对话列表">
      <button
        v-for="conversation in conversations"
        :key="conversation.id"
        class="conversation-row"
        :class="{
          active: conversation.id === currentId,
          warning: conversation.list_state === 'warning',
          failed: conversation.list_state === 'failed',
        }"
        data-testid="conversation-item"
        @click="$emit('select', conversation.id)"
      >
        <span class="conversation-icon">
          <MessageSquare :size="15" />
          <i v-if="conversation.is_running" class="running-pip"></i>
        </span>
        <span class="conversation-copy">
          <strong>{{ compactTitle(conversation.title) }}</strong>
          <small :title="conversation.last_error || ''">
            <span
              class="conversation-list-state"
              :class="`is-${conversation.list_state || 'completed'}`"
              data-testid="conversation-list-state"
            >{{ conversationStateLabel(conversation) }}</span>
            <span aria-hidden="true">·</span>
            {{ formatRelativeTime(conversation.updated_at) }}
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

      <button
        v-if="hasMore"
        class="conversation-load-more"
        type="button"
        :disabled="loadingMore"
        @click="$emit('load-more')"
      >
        <LoaderCircle v-if="loadingMore" class="spin" :size="14" />
        {{ loadingMore ? '加载中' : '加载更多' }}
      </button>

      <div v-if="!conversations.length && !loading" class="sidebar-empty">
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
      <div class="sidebar-utility-group" data-testid="sidebar-account-group">
        <details ref="accountMenu" class="sidebar-account-menu">
          <summary
            class="sidebar-account-button"
            data-testid="sidebar-user-menu"
            :title="accountName"
          >
            <span class="sidebar-account-avatar">{{ accountInitial }}</span>
            <span class="sidebar-account-copy">
              <strong>{{ accountName }}</strong>
              <small>@{{ currentUser.username }} · {{ accountRole }}</small>
            </span>
            <ChevronRight class="sidebar-account-chevron" :size="14" />
          </summary>
          <div class="sidebar-account-popover">
            <button
              type="button"
              data-testid="mode-settings"
              :class="{ active: settingsActive }"
              :aria-pressed="settingsActive"
              @click="openSettings"
            >
              <Settings2 :size="14" />
              系统设置
            </button>
            <button type="button" class="danger" @click="logout">
              <LogOut :size="14" />
              退出登录
            </button>
          </div>
        </details>
      </div>
    </footer>

    <div
      class="sidebar-resize-handle"
      role="separator"
      aria-label="调整会话侧栏宽度"
      aria-orientation="vertical"
      :aria-valuemin="232"
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

