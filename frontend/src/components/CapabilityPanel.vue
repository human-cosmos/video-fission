<script setup>
import { Braces, ChevronRight, Plus, ShieldCheck, Sparkles, Wrench, X } from 'lucide-vue-next'
import { ref } from 'vue'

defineProps({
  skills: { type: Array, default: () => [] },
  tools: { type: Array, default: () => [] },
  mobileOpen: { type: Boolean, default: false },
})

const emit = defineEmits([
  'create-skill',
  'edit-skill',
  'delete-skill',
  'toggle-skill',
  'use-skill',
  'close-mobile',
])

const tab = ref('skills')
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

    <div class="capability-tabs">
      <button :class="{ active: tab === 'skills' }" @click="tab = 'skills'">
        <Sparkles :size="14" /> Skills
        <span>{{ skills.length }}</span>
      </button>
      <button :class="{ active: tab === 'tools' }" @click="tab = 'tools'">
        <Wrench :size="14" /> Tools
        <span>{{ tools.length }}</span>
      </button>
    </div>

    <div v-if="tab === 'skills'" class="capability-content">
      <div class="capability-intro">
        <p>把稳定工作方法写成 Skill，让每次对话都从同一套标准出发。</p>
        <button class="text-button" data-testid="create-skill" @click="$emit('create-skill')">
          <Plus :size="14" /> 新建 Skill
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
            <small>{{ skill.source === 'bundled' ? '内置规范' : '我的 Skill' }}</small>
          </div>
          <label class="switch" :title="skill.read_only ? '内置 Skill 始终启用' : '启用或停用'">
            <input
              type="checkbox"
              :checked="skill.enabled"
              :disabled="skill.read_only"
              @change="$emit('toggle-skill', skill.slug, $event.target.checked)"
            />
            <span></span>
          </label>
        </div>
        <p>{{ skill.description }}</p>
        <div class="skill-actions">
          <button @click="$emit('use-skill', skill)">用于对话 <ChevronRight :size="13" /></button>
          <button v-if="!skill.read_only" @click="$emit('edit-skill', skill)">编辑</button>
          <button
            v-if="!skill.read_only"
            class="danger"
            @click="$emit('delete-skill', skill)"
          >
            删除
          </button>
        </div>
      </article>
    </div>

    <div v-else class="capability-content">
      <div class="capability-intro">
        <p>动态 Tool 由应用注册并安全执行，调用记录会进入当前对话轨迹。</p>
        <span class="verified-pill"><ShieldCheck :size="13" /> 沙箱受控</span>
      </div>
      <details v-for="tool in tools" :key="tool.name" class="tool-card">
        <summary>
          <span class="tool-icon"><Braces :size="15" /></span>
          <div>
            <h3>{{ tool.title }}</h3>
            <code>{{ tool.name }}</code>
          </div>
          <ChevronRight :size="14" />
        </summary>
        <p>{{ tool.description }}</p>
        <pre>{{ JSON.stringify(tool.input_schema, null, 2) }}</pre>
      </details>
    </div>
  </aside>
</template>

