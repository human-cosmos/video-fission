<script setup>
import { X } from 'lucide-vue-next'
import { reactive, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  skill: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'save'])
const form = reactive({
  slug: '',
  name: '',
  description: '',
  body: '',
})

watch(
  () => [props.open, props.skill],
  () => {
    form.slug = props.skill?.slug || ''
    form.name = props.skill?.name || ''
    form.description = props.skill?.description || ''
    form.body =
      props.skill?.content ||
      '## 工作步骤\n\n1. 先确认目标与约束。\n2. 给出可执行的拆解。\n3. 在交付前做一次完整性检查。'
  },
  { immediate: true },
)

function submit() {
  emit('save', {
    slug: form.slug.trim() || undefined,
    name: form.name.trim(),
    description: form.description.trim(),
    body: form.body.trim(),
  })
}
</script>

<template>
  <div v-if="open" class="modal-backdrop" @click.self="$emit('close')">
    <form class="modal-card skill-editor" data-testid="skill-editor" @submit.prevent="submit">
      <header>
        <div>
          <span class="micro-label">SKILL WORKSHOP</span>
          <h2>{{ skill ? '编辑 Skill' : '创建 Skill' }}</h2>
        </div>
        <button type="button" class="icon-button" aria-label="关闭" @click="$emit('close')">
          <X :size="19" />
        </button>
      </header>
      <label>
        <span>显示名称</span>
        <input v-model="form.name" required maxlength="80" placeholder="例如：爆款开场拆解" />
      </label>
      <label v-if="!skill">
        <span>标识（可选）</span>
        <input v-model="form.slug" pattern="[a-zA-Z0-9-]*" placeholder="hook-analysis" />
      </label>
      <label>
        <span>一句话描述</span>
        <input
          v-model="form.description"
          required
          maxlength="500"
          placeholder="说明这个 Skill 在什么场景下解决什么问题"
        />
      </label>
      <label class="body-field">
        <span>工作说明</span>
        <textarea v-model="form.body" required rows="10"></textarea>
      </label>
      <footer>
        <button type="button" class="button subtle" @click="$emit('close')">取消</button>
        <button type="submit" class="button primary" :disabled="busy">
          {{ busy ? '保存中…' : '保存 Skill' }}
        </button>
      </footer>
    </form>
  </div>
</template>

