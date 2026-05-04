<template>
  <div class="page-header">
    <div class="header-left">
      <h2 class="header-title">{{ title }}</h2>
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
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg1);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.header-title {
  font-size: 13px;
  font-weight: 600;
  font-family: var(--mono);
  color: var(--t1);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
