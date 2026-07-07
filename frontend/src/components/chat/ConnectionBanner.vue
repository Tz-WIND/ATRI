<template>
  <Transition name="conn-banner">
    <div
      v-if="visible"
      class="connection-banner"
      role="status"
      aria-live="polite"
    >
      <span
        class="banner-dot"
        :class="{ pending: reconnectPending }"
      />
      <span class="banner-text">
        <template v-if="reconnectPending">
          Reconnecting<span class="banner-eta"> · retry in {{ etaSeconds }}s</span>…
        </template>
        <template v-else>
          Connection lost
        </template>
      </span>
      <button
        type="button"
        class="banner-retry"
        :disabled="reconnectPending"
        @click="$emit('reconnect')"
      >
        Reconnect now
      </button>
    </div>
  </Transition>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  openedOnce: { type: Boolean, default: false },
  connected: { type: Boolean, default: false },
  reconnectDelayMs: { type: Number, default: 0 },
})

defineEmits(['reconnect'])

// Initial connection attempts are normal loading, not a connection-loss state.
const visible = computed(() => props.openedOnce && !props.connected)

const reconnectPending = computed(() => props.reconnectDelayMs > 0)

// Count down the delay in seconds for display. We snapshot at the start of each
// scheduled delay and tick once per second until it elapses.
const etaSeconds = ref(0)
let tickTimer = null

function clearTick() {
  if (tickTimer) {
    clearInterval(tickTimer)
    tickTimer = null
  }
}

onUnmounted(clearTick)

function startTick(delayMs) {
  clearTick()
  const seconds = Math.max(1, Math.round(delayMs / 1000))
  etaSeconds.value = seconds
  let remaining = seconds
  tickTimer = setInterval(() => {
    remaining -= 1
    if (remaining <= 0) {
      etaSeconds.value = 0
      clearTick()
      return
    }
    etaSeconds.value = remaining
  }, 1000)
}

watch(
  () => props.reconnectDelayMs,
  (next) => {
    if (next > 0) {
      startTick(next)
    } else {
      clearTick()
      etaSeconds.value = 0
    }
  },
)

watch(
  () => props.connected,
  (next) => {
    if (next) {
      clearTick()
      etaSeconds.value = 0
    }
  },
)
</script>

<style scoped>
.connection-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  background: rgba(255, 141, 127, 0.12);
  border-bottom: 1px solid rgba(255, 141, 127, 0.28);
  color: var(--t2);
  font-size: 12px;
  font-family: var(--mono);
  flex-shrink: 0;
}

.banner-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--red);
  animation: banner-pulse 1.4s ease-in-out infinite;
}

.banner-dot.pending {
  background: var(--orange);
  animation: banner-pulse 0.9s ease-in-out infinite;
}

@keyframes banner-pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

.banner-text {
  flex: 1;
  min-width: 0;
  color: var(--t1);
}

.banner-eta {
  color: var(--t3);
}

.banner-retry {
  padding: 2px 9px;
  font-size: 11px;
  font-family: var(--mono);
  font-weight: 600;
  color: var(--t1);
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.14s ease, border-color 0.14s ease, opacity 0.14s ease;
}

.banner-retry:hover:not(:disabled) {
  background: var(--bg-200);
  border-color: var(--orange);
}

.banner-retry:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.conn-banner-enter-active,
.conn-banner-leave-active {
  transition: max-height 0.22s cubic-bezier(0.22, 1, 0.36, 1),
              opacity 0.18s ease;
  overflow: hidden;
}

.conn-banner-enter-from,
.conn-banner-leave-to {
  max-height: 0;
  opacity: 0;
  padding-top: 0;
  padding-bottom: 0;
}

.conn-banner-enter-to,
.conn-banner-leave-from {
  max-height: 40px;
  opacity: 1;
}
</style>
