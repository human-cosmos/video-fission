<script setup>
import {
  Archive,
  ArchiveRestore,
  LoaderCircle,
  MessageSquare,
  Plus,
  Search,
  X,
} from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { compactTitle, formatRelativeTime } from '../utils/events'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  currentId: { type: String, default: '' },
  showArchived: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  mobileOpen: { type: Boolean, default: false },
})

const emit = defineEmits([
  'new',
  'select',
  'archive',
  'unarchive',
  'toggle-archived',
  'close-mobile',
])

const query = ref('')
const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return props.conversations
  return props.conversations.filter((item) =>
    `${item.title} ${item.last_error || ''}`.toLowerCase().includes(needle),
  )
})
</script>

<template>
  <aside class="sidebar" :class="{ 'is-mobile-open': mobileOpen }">
    <header class="brand-row">
      <div class="brand-mark" aria-hidden="true">
        <span></span>
        <span></span>
        <span></span>
      </div>
      <div class="brand-copy">
        <strong>VIDEO FISSION</strong>
        <small>CREATIVE OPERATIONS</small>
      </div>
      <button class="icon-button sidebar-close" aria-label="关闭会话列表" @click="$emit('close-mobile')">
        <X :size="18" />
      </button>
    </header>

    <button class="new-chat-button" data-testid="new-conversation" @click="$emit('new')">
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

    <footer class="sidebar-footer">
      <button class="archive-toggle" @click="$emit('toggle-archived')">
        <Archive :size="14" />
        {{ showArchived ? '返回最近对话' : '查看归档' }}
      </button>
    </footer>
  </aside>
</template>

