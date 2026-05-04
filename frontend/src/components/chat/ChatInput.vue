<template>
  <div class="input-area">
    <div class="input-wrap">
      <textarea
        ref="textarea"
        v-model="text"
        placeholder="Send a message..."
        :disabled="sending"
        rows="1"
        @keydown="onKeydown"
        @input="autoResize"
      ></textarea>
      <div class="input-toolbar">
        <div class="tools-left">
          <ModelSelector />
        </div>
        <div class="tools-right">
          <button class="icon-btn" title="Attach image" @click="$emit('attach')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>
            </svg>
          </button>
          <button
            class="btn-send"
            :disabled="!text.trim() || sending"
            @click="send"
            title="Send"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import ModelSelector from './ModelSelector.vue'

const props = defineProps({
  sending: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'attach'])

const text = ref('')
const textarea = ref(null)

function autoResize() {
  nextTick(() => {
    const el = textarea.value
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  })
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function send() {
  if (!text.value.trim() || props.sending) return
  emit('send', text.value)
  text.value = ''
  nextTick(autoResize)
}
</script>

<style scoped>
.input-area {
  padding: 12px 16px 16px;
  flex-shrink: 0;
}

.input-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 920px;
  margin: 0 auto;
  background: rgba(37, 37, 38, 0.92);
  border: 1px solid var(--border-input);
  border-radius: 13px;
  padding: 12px 12px 10px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.04);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.input-wrap:focus-within {
  border-color: rgba(255, 255, 255, 0.22);
  box-shadow: 0 14px 38px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.03);
}

textarea {
  width: 100%;
  background: transparent;
  border: none;
  color: var(--t1);
  padding: 0 2px;
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.5;
  resize: none;
  min-height: 28px;
  max-height: 180px;
  outline: none;
}

textarea::placeholder {
  color: var(--t3);
}

textarea:disabled {
  opacity: 0.6;
}

.input-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tools-left,
.tools-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.icon-btn {
  width: 31px;
  height: 31px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--t3);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}

.icon-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--t1);
}

.icon-btn svg {
  width: 16px;
  height: 16px;
}

.btn-send {
  width: 32px;
  height: 32px;
  background: #e8e8e8;
  color: #1f1f1f;
  border: none;
  border-radius: 50%;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.12s, transform 0.12s;
}

.btn-send:hover {
  background: #fff;
  transform: translateY(-1px);
}

.btn-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  transform: none;
}

.btn-send svg {
  width: 16px;
  height: 16px;
}
</style>
