<template>
  <div class="empty-state">
    <div class="empty-icon" v-if="icon" v-html="icon"></div>
    <p class="empty-text">{{ text }}</p>
    <button v-if="action" class="btn btn-primary" @click="$emit('action')">{{ action }}</button>
  </div>
</template>

<script setup>
defineProps({
  text: { type: String, required: true },
  icon: { type: String, default: '' },
  action: { type: String, default: '' },
})
defineEmits(['action'])
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 20px;
  color: var(--t3);
  gap: 10px;
}

.empty-icon {
  width: 40px;
  height: 40px;
  color: var(--t3);
  margin-bottom: 4px;
}

.empty-icon :deep(svg) {
  width: 40px;
  height: 40px;
}

.empty-text {
  font-size: 13px;
}

.btn {
  padding: 5px 14px;
  border-radius: 6px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  font-family: var(--mono);
  transition: all 0.12s;
  margin-top: 8px;
}

.btn-primary {
  background: var(--acc-bg);
  color: var(--acc2);
  border-color: rgba(0, 122, 204, 0.3);
}

.btn-primary:hover {
  background: rgba(0, 122, 204, 0.22);
}
</style>
