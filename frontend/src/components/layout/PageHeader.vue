<template>
  <div class="page-header">
    <div class="header-left">
      <h2 class="header-title">
        {{ title }}
      </h2>
      <slot name="status" />
    </div>
    <div class="header-right">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
})
</script>

<style scoped>
.page-header {
  height: var(--header-h);
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: rgba(24, 24, 24, 0.68);
  backdrop-filter: blur(18px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.header-title {
  font-size: 14px;
  font-weight: 600;
  font-family: var(--sans);
  color: var(--t1);
  letter-spacing: 0;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
