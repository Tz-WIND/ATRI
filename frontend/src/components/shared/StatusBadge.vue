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
  padding: 1px 8px;
  border-radius: 10px;
  font-family: var(--mono);
  font-weight: 500;
}

.badge.on {
  background: var(--green-bg);
  color: var(--green);
}

.badge.off {
  background: var(--red-bg);
  color: var(--red);
}

.badge.info {
  background: var(--acc-bg);
  color: var(--acc2);
}

.badge.default {
  background: var(--bg3);
  color: var(--t2);
}

.badge-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
</style>
