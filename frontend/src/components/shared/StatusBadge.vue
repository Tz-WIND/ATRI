<template>
  <span :class="['badge', type]">
    <span
      v-if="dot"
      class="badge-dot"
    />
    <slot />
  </span>
</template>

<script setup>
defineProps({
  type: {
    type: String,
    default: 'default',
    validator: v => ['on', 'off', 'default', 'info'].includes(v),
  },
  dot: { type: Boolean, default: false },
})
</script>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 999px;
  font-family: var(--mono);
  font-weight: 500;
  border: 1px solid transparent;
}

.badge.on {
  background: var(--ok-bg);
  color: var(--ok);
  border-color: rgba(143, 216, 199, 0.22);
}

.badge.off {
  background: var(--red-bg);
  color: var(--red);
  border-color: rgba(255, 141, 127, 0.22);
}

.badge.info {
  background: var(--acc-bg);
  color: var(--acc2);
  border-color: rgba(125, 168, 232, 0.22);
}

.badge.default {
  background: var(--bg-100);
  color: var(--t2);
  border-color: var(--border-light);
}

.badge-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
</style>
