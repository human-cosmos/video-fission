<script setup>
import { Check, ChevronDown } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  ariaLabel: { type: String, required: true },
})

const emit = defineEmits(['update:modelValue', 'change'])
const open = ref(false)
const root = ref(null)
const trigger = ref(null)
const menu = ref(null)
const activeIndex = ref(0)

const selectedIndex = computed(() =>
  props.options.findIndex((option) => option.value === props.modelValue),
)
const selectedOption = computed(() => props.options[selectedIndex.value] || null)
const displayValue = computed(
  () => selectedOption.value?.label || props.modelValue || '默认',
)

function focusOption(index) {
  const items = menu.value?.querySelectorAll('[role="option"]')
  items?.[index]?.focus()
}

function openMenu(index = Math.max(selectedIndex.value, 0)) {
  if (!props.options.length) return
  open.value = true
  activeIndex.value = Math.min(Math.max(index, 0), props.options.length - 1)
  nextTick(() => focusOption(activeIndex.value))
}

function closeMenu({ restoreFocus = false } = {}) {
  open.value = false
  if (restoreFocus) nextTick(() => trigger.value?.focus())
}

function choose(option) {
  emit('update:modelValue', option.value)
  emit('change', option.value)
  closeMenu({ restoreFocus: true })
}

function moveActive(delta) {
  if (!props.options.length) return
  if (!open.value) {
    openMenu()
    return
  }
  activeIndex.value =
    (activeIndex.value + delta + props.options.length) % props.options.length
  nextTick(() => focusOption(activeIndex.value))
}

function onTriggerKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(event.key === 'ArrowDown' ? 1 : -1)
  } else if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    closeMenu({ restoreFocus: true })
  }
}

function onMenuKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(event.key === 'ArrowDown' ? 1 : -1)
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    activeIndex.value = event.key === 'Home' ? 0 : props.options.length - 1
    nextTick(() => focusOption(activeIndex.value))
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeMenu({ restoreFocus: true })
  } else if (event.key === 'Tab') {
    closeMenu()
  }
}

function onDocumentPointerDown(event) {
  if (open.value && !root.value?.contains(event.target)) closeMenu()
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown, true))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerDown, true))
</script>

<template>
  <div ref="root" class="select-control" :class="{ 'is-open': open }">
    <button
      ref="trigger"
      type="button"
      class="select-trigger"
      :aria-label="ariaLabel"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="open ? closeMenu() : openMenu()"
      @keydown="onTriggerKeydown"
    >
      <span class="select-icon"><slot name="icon" /></span>
      <span class="select-copy">
        <span class="select-caption">{{ caption }}</span>
        <span class="select-value">{{ displayValue }}</span>
      </span>
      <ChevronDown class="select-chevron" :size="14" aria-hidden="true" />
    </button>

    <Transition name="select-pop">
      <div
        v-if="open"
        ref="menu"
        class="select-menu"
        role="listbox"
        :aria-label="ariaLabel"
        @keydown="onMenuKeydown"
      >
        <button
          v-for="(option, index) in options"
          :key="option.value"
          type="button"
          class="select-option"
          :class="{ selected: option.value === modelValue }"
          role="option"
          :aria-selected="option.value === modelValue"
          @click="choose(option)"
          @focus="activeIndex = index"
        >
          <span class="option-marker" aria-hidden="true"></span>
          <span class="option-copy">
            <span class="option-label">{{ option.label }}</span>
            <small v-if="option.detail">{{ option.detail }}</small>
          </span>
          <span v-if="option.meta" class="option-meta">{{ option.meta }}</span>
          <Check v-if="option.value === modelValue" :size="15" aria-hidden="true" />
        </button>
      </div>
    </Transition>
  </div>
</template>
