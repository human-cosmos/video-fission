<script setup>
import { ChevronRight, Settings2, Sparkles, X } from '@lucide/vue'

defineProps({
  skills: { type: Array, default: () => [] },
  mobileOpen: { type: Boolean, default: false },
})

defineEmits([
  'use-skill',
  'open-settings',
  'close-mobile',
])

</script>

<template>
  <aside class="capability-panel" :class="{ 'is-mobile-open': mobileOpen }">
    <header class="capability-header">
      <div>
        <span class="micro-label">CAPABILITY DECK</span>
        <h2>能力台</h2>
      </div>
      <button class="icon-button panel-close" aria-label="关闭能力台" @click="$emit('close-mobile')">
        <X :size="18" />
      </button>
    </header>

    <div class="capability-content">
      <div class="capability-intro">
        <p>Skill 与 Tool 为全局能力，可在这里选择用于当前对话。</p>
        <button class="text-button" @click="$emit('open-settings')">
          <Settings2 :size="14" /> 系统设置
        </button>
      </div>

      <article
        v-for="skill in skills"
        :key="`${skill.source}-${skill.slug}`"
        class="skill-card"
        :class="{ disabled: !skill.enabled }"
      >
        <div class="skill-head">
          <span class="skill-icon"><Sparkles :size="15" /></span>
          <div>
            <h3>{{ skill.name }}</h3>
            <small>{{ skill.source === 'bundled' ? '内置规范' : '全局 Skill' }}</small>
          </div>
          <span class="verified-pill">{{ skill.enabled ? '已启用' : '已停用' }}</span>
        </div>
        <p>{{ skill.description }}</p>
        <div class="skill-actions">
          <button @click="$emit('use-skill', skill)">用于对话 <ChevronRight :size="13" /></button>
        </div>
      </article>
    </div>

  </aside>
</template>

