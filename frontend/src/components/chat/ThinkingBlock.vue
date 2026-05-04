<template>
  <div :class="['thinking-block', thinking.done ? 'done' : 'active']">
    <div class="thinking-header" @click="open = !open">
      <svg :class="['think-chevron', { open }]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="9 18 15 12 9 6" />
      </svg>
      <span class="think-icon">&#9679;</span>
      <span class="think-label">{{ thinking.done ? `Thought for ${elapsed}s` : 'Thinking...' }}</span>
      <span class="think-dur" v-if="!thinking.done">{{ elapsed }}s</span>
    </div>
    <div :class="['thinking-content', { open }]">
      <pre>{{ thinking.content }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({
  thinking: { type: Object, required: true },
})

const open = ref(true)
const now = ref(Date.now())

const elapsed = computed(() => {
  if (!props.thinking.startTime) return '0.0'
  return ((now.value - props.thinking.startTime) / 1000).toFixed(1)
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

// Auto-close when done
watch(() => props.thinking.done, (done) => {
  if (done) open.value = false
})
</script>

<style scoped>
.thinking-block {
  margin: 8px auto;
  max-width: 900px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--bg1);
  font-family: var(--mono);
  font-size: 12px;
  transition: border-color 0.2s;
}

.thinking-block.active {
  border-color: rgba(197, 134, 192, 0.35);
}

.thinking-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  cursor: pointer;
  color: var(--purple);
  transition: background 0.12s;
  user-select: none;
}

.thinking-header:hover {
  background: var(--bg2);
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
  flex-shrink: 0;
}

.thinking-block.active .think-icon {
  animation: pulse-think 1.5s ease-in-out infinite;
}

@keyframes pulse-think {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

.think-label {
  flex: 1;
  color: var(--t2);
}

.think-dur {
  color: var(--t3);
  font-size: 11px;
}

.thinking-content {
  display: none;
  padding: 0 12px 8px;
}

.thinking-content.open {
  display: block;
}

.thinking-content pre {
  margin: 0;
  padding: 8px;
  background: var(--bg0);
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--t2);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow: auto;
}
</style>
