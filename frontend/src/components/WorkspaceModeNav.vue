<script setup>
import { WORKSPACE_MODES } from '../utils/format'

defineProps({
  modelValue: {
    type: String,
    required: true,
  },
})

defineEmits(['update:modelValue'])
</script>

<template>
  <nav class="workspace-mode-nav" aria-label="工作台模式">
    <button
      v-for="item in WORKSPACE_MODES"
      :key="item.id"
      type="button"
      :class="{ active: modelValue === item.id }"
      :data-testid="`mode-${item.id}`"
      :aria-pressed="modelValue === item.id"
      @click="$emit('update:modelValue', item.id)"
    >
      {{ item.label }}
    </button>
  </nav>
</template>
