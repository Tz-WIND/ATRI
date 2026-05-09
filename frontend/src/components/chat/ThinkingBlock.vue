<template>
  <div :class="['thinking-block', thinking.done ? 'done' : 'active']">
    <button
      class="thinking-header"
      type="button"
      @click="open = !open"
    >
      <svg
        :class="['think-chevron', { open }]"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
      >
        <polyline points="9 18 15 12 9 6" />
      </svg>
      <span class="think-icon" />
      <span class="think-label">{{ thinking.done ? `Thought for ${elapsed}s` : 'Thinking' }}</span>
      <span
        v-if="!thinking.done"
        class="think-dur"
      >{{ elapsed }}s</span>
    </button>
    <div
      v-if="thinking.content"
      :class="['thinking-content', { open }]"
    >
      <pre>{{ thinking.content }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  thinking: { type: Object, required: true },
})

const open = ref(true)
const now = ref(Date.now())

const elapsed = computed(() => {
  if (!props.thinking.startTime) return '0.0'
  const end = props.thinking.done && props.thinking.endTime ? props.thinking.endTime : now.value
  return ((end - props.thinking.startTime) / 1000).toFixed(1)
})

let timer = null
onMounted(() => {
  timer = setInterval(() => {
    if (!props.thinking.done) {
      now.value = Date.now()
    }
  }, 100)
})

onUnmounted(() => {
  clearInterval(timer)
})
</script>

<style scoped>
.thinking-block {
  margin: 10px auto 14px;
  padding: 5px 0 6px;
  max-width: 900px;
  color: var(--t3);
  font-size: 13px;
}

.thinking-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  color: var(--t3);
  user-select: none;
  text-align: left;
  font-family: var(--mono);
  font-size: 12px;
}

.thinking-header:hover {
  color: var(--t2);
}

.think-chevron {
  width: 12px;
  height: 12px;
  color: var(--t3);
  transition: transform 0.15s;
  flex-shrink: 0;
}

.think-chevron.open {
  transform: rotate(90deg);
}

.think-icon {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--acc2);
  color: var(--acc2);
}

.thinking-block.active .think-icon {
  animation: pulse-think 1.5s ease-in-out infinite;
}

@keyframes pulse-think {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

.think-label {
  flex: 0 0 auto;
  color: inherit;
  font-weight: 600;
}

.think-dur {
  color: var(--t3);
  font-size: 11px;
}

.thinking-content {
  display: none;
  padding: 6px 0 0 34px;
}

.thinking-content.open {
  display: block;
}

.thinking-content pre {
  margin: 0;
  padding: 0;
  background: transparent;
  font-size: 13px;
  line-height: 1.65;
  color: rgba(204, 204, 204, 0.58);
  font-family: var(--sans);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
}
</style>
