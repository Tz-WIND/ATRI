<template>
  <div class="settings-page">
    <PageHeader title="Settings" />
    <div class="settings-body">
      <!-- Provider Workbench -->
      <section class="form-section">
        <h3>Model Providers</h3>
        <ProviderWorkbench />
      </section>

      <!-- Active Models -->
      <section class="form-section">
        <h3>Active Models</h3>
        <p class="section-desc">Models enabled for use in Chat. Switch between them from the chat input bar.</p>
        <ActiveModelsList />
      </section>

      <!-- Generation Params -->
      <section class="form-section">
        <h3>Generation Parameters</h3>
        <div class="field-row">
          <div class="field">
            <label>Max Tokens</label>
            <input v-model.number="form.max_tokens" type="number" />
          </div>
          <div class="field">
            <label>Temperature</label>
            <input v-model.number="form.temperature" type="number" step="0.1" />
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Max Context Tokens</label>
            <input v-model.number="form.max_context_tokens" type="number" />
          </div>
          <div class="field">
            <label>Max Rounds</label>
            <input v-model.number="form.max_rounds" type="number" />
          </div>
        </div>
      </section>

      <!-- Agent Behavior -->
      <section class="form-section">
        <h3>Agent Behavior</h3>
        <div class="field">
          <label>Wake Words (comma separated)</label>
          <input v-model="form.wake_words" placeholder="atri, hey" />
        </div>
        <div class="field">
          <label>Persona</label>
          <textarea v-model="form.persona" rows="2" placeholder="Agent personality..."></textarea>
        </div>
        <div class="field">
          <label>Extra Instructions</label>
          <textarea v-model="form.extra_instructions" rows="3" placeholder="Additional system prompt..."></textarea>
        </div>
      </section>

      <!-- Web Search -->
      <section class="form-section">
        <h3>Web Search</h3>
        <p class="section-desc">Configure a Tavily API key for higher-quality web search. Leave empty to use DuckDuckGo (free).</p>
        <div class="field">
          <label>Tavily API Key</label>
          <input v-model="form.tavily_api_key" type="password" placeholder="tvly-..." />
        </div>
      </section>

      <!-- Music Library -->
      <section class="form-section">
        <h3>Music Library</h3>
        <p class="section-desc">Directories to scan for music files. Supports FLAC, MP3, WAV, M4A, OGG, AAC, and more.</p>
        <div class="music-dirs">
          <div class="music-dir-row" v-for="(dir, i) in musicDirs" :key="i">
            <input
              v-model="musicDirs[i]"
              placeholder="C:\Music or /home/user/music"
              class="music-dir-input"
            />
            <button class="btn-icon-sm btn-danger" @click="musicDirs.splice(i, 1)" title="Remove">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <button class="btn btn-outline" @click="musicDirs.push('')">+ Add Directory</button>
        </div>
        <button class="btn btn-secondary" @click="saveMusicDirs" :disabled="savingMusic" style="margin-top:8px;">
          {{ savingMusic ? 'Saving...' : 'Save Music Directories' }}
        </button>
      </section>

      <button class="btn btn-primary" @click="saveSettings" :disabled="saving">
        {{ saving ? 'Saving...' : 'Save Settings' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import ProviderWorkbench from './ProviderWorkbench.vue'
import ActiveModelsList from './ActiveModelsList.vue'
import { useApi } from '@/composables/useApi.js'
import { useProviders } from '@/composables/useProviders.js'

const api = useApi()
const { loadProviders, loadStatus } = useProviders()

const form = ref({
  max_tokens: 4096,
  temperature: 0,
  max_context_tokens: 128000,
  max_rounds: 50,
  wake_words: '',
  persona: '',
  extra_instructions: '',
  tavily_api_key: '',
})

const saving = ref(false)
const savingMusic = ref(false)
const musicDirs = ref([''])

async function loadSettings() {
  try {
    const d = await api.getSettings()
    form.value.max_tokens = d.max_tokens || 4096
    form.value.temperature = d.temperature || 0
    form.value.max_context_tokens = d.max_context_tokens || 128000
    form.value.max_rounds = d.max_rounds || 50
    form.value.wake_words = (d.wake_words || []).join(', ')
    form.value.persona = d.persona || ''
    form.value.extra_instructions = d.extra_instructions || ''
    form.value.tavily_api_key = d.tavily_api_key || ''
  } catch {}
}

async function loadMusicDirs() {
  try {
    const d = await api.musicDirs()
    if (d.directories && d.directories.length > 0) {
      musicDirs.value = d.directories
    }
  } catch {}
}

async function saveMusicDirs() {
  savingMusic.value = true
  try {
    const dirs = musicDirs.value.map(s => s.trim()).filter(Boolean)
    await api.saveMusicDirs(dirs)
  } finally {
    savingMusic.value = false
  }
}

async function saveSettings() {
  saving.value = true
  try {
    await api.saveSettings({
      max_tokens: form.value.max_tokens,
      temperature: form.value.temperature,
      max_context_tokens: form.value.max_context_tokens,
      max_rounds: form.value.max_rounds,
      wake_words: form.value.wake_words.split(',').map(s => s.trim()).filter(Boolean),
      persona: form.value.persona,
      extra_instructions: form.value.extra_instructions,
      tavily_api_key: form.value.tavily_api_key,
    })
    await loadStatus()
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadProviders(), loadStatus(), loadSettings(), loadMusicDirs()])
})
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.settings-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.form-section {
  margin-bottom: 24px;
}

.form-section h3 {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--t3);
  margin-bottom: 12px;
  font-family: var(--mono);
}

.section-desc {
  font-size: 12px;
  color: var(--t3);
  margin-bottom: 10px;
}

.field {
  margin-bottom: 12px;
}

.field label {
  display: block;
  font-size: 12px;
  color: var(--t2);
  margin-bottom: 4px;
  font-family: var(--mono);
}

.field input,
.field select,
.field textarea {
  width: 100%;
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--t1);
  padding: 8px 12px;
  font-size: 13px;
  font-family: var(--mono);
  outline: none;
}

.field input:focus,
.field select:focus,
.field textarea:focus {
  border-color: var(--acc);
}

.field textarea {
  resize: vertical;
}

.field-row {
  display: flex;
  gap: 12px;
}

.field-row .field {
  flex: 1;
}

.btn {
  padding: 6px 16px;
  border-radius: 6px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  transition: all 0.12s;
  font-family: var(--mono);
}

.btn-primary {
  background: rgba(63, 185, 80, 0.15);
  color: var(--green);
  border-color: rgba(63, 185, 80, 0.3);
}

.btn-primary:hover {
  background: rgba(63, 185, 80, 0.25);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  background: rgba(0, 122, 204, 0.12);
  color: var(--acc2);
  border-color: rgba(0, 122, 204, 0.25);
}
.btn-secondary:hover {
  background: rgba(0, 122, 204, 0.2);
}
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-outline {
  background: transparent;
  color: var(--t3);
  border-color: var(--border);
}
.btn-outline:hover {
  background: var(--bg2);
  color: var(--t2);
}

.music-dirs {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.music-dir-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.music-dir-input {
  flex: 1;
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--t1);
  padding: 8px 12px;
  font-size: 13px;
  font-family: var(--mono);
  outline: none;
}
.music-dir-input:focus {
  border-color: var(--acc);
}

.btn-icon-sm {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  cursor: pointer;
  border-radius: 4px;
  color: var(--t3);
  transition: all 0.12s;
}
.btn-icon-sm:hover {
  background: var(--bg2);
}
.btn-icon-sm svg {
  width: 14px;
  height: 14px;
}
.btn-danger:hover {
  color: var(--red);
  background: var(--red-bg);
}
</style>
