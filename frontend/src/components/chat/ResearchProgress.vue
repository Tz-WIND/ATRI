<template>
  <transition name="research-rail">
    <section
      v-if="status.visible"
      class="research-progress"
      :class="{ active: status.active }"
      aria-live="polite"
      aria-label="Deep Research progress"
    >
      <div class="research-phase">
        <span class="phase-mark" />
        <span class="phase-kicker">Research</span>
        <span class="phase-name">{{ phaseLabel }}</span>
      </div>
      <dl class="research-metrics">
        <div
          v-for="metric in metrics"
          :key="metric.label"
          class="research-metric"
        >
          <dt>{{ metric.label }}</dt>
          <dd>{{ metric.value }}</dd>
        </div>
      </dl>
    </section>
  </transition>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: {
    type: Object,
    default: () => ({
      visible: false,
      active: false,
      phase: '',
      evidenceCount: 0,
      toolCalls: 0,
      webFetches: 0,
      activeSubagents: 0,
      totalSubagents: 0,
    }),
  },
})

const phaseLabel = computed(() => {
  const phase = String(props.status.phase || 'starting').replaceAll('_', ' ')
  return phase.charAt(0).toUpperCase() + phase.slice(1)
})

const metrics = computed(() => [
  { label: 'Evidence', value: props.status.evidenceCount || 0 },
  { label: 'Calls', value: props.status.toolCalls || 0 },
  { label: 'Pages', value: props.status.webFetches || 0 },
  {
    label: 'Agents',
    value: `${props.status.activeSubagents || 0}/${props.status.totalSubagents || 0}`,
  },
])
</script>

<style scoped>
.research-progress {
  position: sticky;
  top: 6px;
  z-index: 4;
  display: grid;
  grid-template-columns: minmax(148px, 1.15fr) minmax(248px, 2fr);
  min-height: 44px;
  margin: 4px 12px 10px;
  overflow: hidden;
  color: var(--t2);
  background: rgba(24, 24, 24, 0.94);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.24);
  backdrop-filter: blur(14px);
}

.research-phase {
  display: grid;
  grid-template-columns: 7px auto;
  grid-template-rows: auto auto;
  align-content: center;
  column-gap: 9px;
  padding: 7px 13px;
  border-right: 1px solid var(--border-light);
}

.phase-mark {
  grid-row: 1 / 3;
  align-self: stretch;
  width: 2px;
  min-height: 24px;
  margin-left: 2px;
  background: var(--t4);
  border-radius: 2px;
}

.active .phase-mark {
  background: var(--acc2);
  box-shadow: 0 0 8px rgba(158, 191, 255, 0.38);
  animation: research-signal 1.8s ease-in-out infinite;
}

.phase-kicker {
  color: var(--t4);
  font: 9px/1.1 var(--mono);
  letter-spacing: 0.13em;
  text-transform: uppercase;
}

.phase-name {
  overflow: hidden;
  color: var(--t1);
  font-size: 12px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.research-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(52px, 1fr));
  margin: 0;
}

.research-metric {
  display: grid;
  align-content: center;
  justify-items: end;
  min-width: 0;
  padding: 6px 10px;
  border-right: 1px solid var(--border-light);
}

.research-metric:last-child {
  border-right: 0;
}

.research-metric dt {
  color: var(--t4);
  font: 8px/1.2 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.research-metric dd {
  margin: 1px 0 0;
  color: var(--t1);
  font: 12px/1.2 var(--mono);
  font-variant-numeric: tabular-nums;
}

.research-rail-enter-active,
.research-rail-leave-active {
  transition: opacity 140ms ease, transform 140ms ease;
}

.research-rail-enter-from,
.research-rail-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@keyframes research-signal {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}

@media (max-width: 620px) {
  .research-progress {
    grid-template-columns: 1fr;
  }

  .research-phase {
    border-right: 0;
    border-bottom: 1px solid var(--border-light);
  }
}

@media (prefers-reduced-motion: reduce) {
  .active .phase-mark {
    animation: none;
  }
}
</style>
