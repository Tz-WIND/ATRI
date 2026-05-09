<template>
  <div>
    <div
      v-if="activeModels.length === 0"
      class="empty"
    >
      No models enabled. Enable models from provider model lists above.
    </div>
    <div
      v-for="m in activeModels"
      :key="m.model + m.provider"
      class="model-row"
    >
      <div class="model-info">
        <div :class="['model-name', { active: m.model === activeModel }]">
          {{ m.model }}
        </div>
        <div
          v-if="m.provider"
          class="model-provider"
        >
          {{ m.provider }}
        </div>
      </div>
      <div class="model-actions">
        <span
          v-if="m.model === activeModel"
          class="badge-current"
        >✓ current</span>
        <button
          class="btn btn-remove"
          @click="handleDeactivate(m.provider || '', m.model)"
        >
          Remove
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useProviders } from '@/composables/useProviders.js'

const { activeModels, activeModel, deactivateModel } = useProviders()

function handleDeactivate(provider, model) {
  deactivateModel(provider, model)
}
</script>

<style scoped>
.empty {
  color: var(--t3);
  font-size: 12px;
  padding: 8px 0;
}

.model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 0;
  border-bottom: 1px solid var(--border-light);
}

.model-row:last-child { border-bottom: none; }

.model-info { flex: 1; min-width: 0; }

.model-name {
  font-size: 13px;
  font-weight: 650;
  font-family: var(--mono);
  color: var(--t1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-name.active { color: var(--acc2); }

.model-provider {
  font-size: 11px;
  color: var(--t3);
}

.model-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.badge-current {
  font-size: 11px;
  color: var(--ok);
  font-family: var(--mono);
  margin-right: 6px;
}

.btn {
  padding: 3px 8px;
  border-radius: 7px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 10px;
  font-weight: 600;
  font-family: var(--mono);
  transition: all 0.12s;
}

.btn-remove {
  background: var(--red-bg);
  color: var(--red);
  border-color: rgba(255, 141, 127, 0.25);
}

.btn-remove:hover {
  background: rgba(255, 141, 127, 0.18);
}
</style>
