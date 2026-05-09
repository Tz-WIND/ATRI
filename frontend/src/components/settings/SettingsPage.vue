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
        <p class="section-desc">
          Models enabled for use in Chat. Switch between them from the chat input bar.
        </p>
        <ActiveModelsList />
      </section>

      <!-- Generation Params -->
      <section class="form-section">
        <h3>Generation Parameters</h3>
        <div class="field-row">
          <div class="field">
            <label>Max Tokens</label>
            <input
              v-model.number="form.max_tokens"
              type="number"
            >
          </div>
          <div class="field">
            <label>Temperature</label>
            <input
              v-model.number="form.temperature"
              type="number"
              step="0.1"
            >
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Max Context Tokens</label>
            <input
              v-model.number="form.max_context_tokens"
              type="number"
            >
          </div>
          <div class="field">
            <label>Max Rounds</label>
            <input
              v-model.number="form.max_rounds"
              type="number"
            >
          </div>
        </div>
      </section>

      <!-- Image Transcription -->
      <section class="form-section vision-section">
        <div class="section-title-row">
          <div class="section-heading">
            <button
              type="button"
              class="collapse-button"
              :class="{ open: imageTranscriptionExpanded }"
              :aria-expanded="imageTranscriptionExpanded"
              aria-controls="image-transcription-settings"
              title="Toggle image transcription settings"
              @click="imageTranscriptionExpanded = !imageTranscriptionExpanded"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="m9 18 6-6-6-6" />
              </svg>
            </button>
            <div>
              <h3>Image Transcription Model</h3>
              <p class="section-desc">
                Route image attachments through a dedicated vision model before the main agent sees them.
              </p>
            </div>
          </div>
          <button
            type="button"
            class="switch-field"
            :class="{ active: form.image_transcription.enabled }"
            role="switch"
            :aria-checked="form.image_transcription.enabled"
            @click="form.image_transcription.enabled = !form.image_transcription.enabled"
          >
            <span class="switch-track">
              <span class="switch-thumb" />
            </span>
            <span class="switch-label">
              {{ form.image_transcription.enabled ? 'Enabled' : 'Off' }}
            </span>
          </button>
        </div>
        <div
          v-show="imageTranscriptionExpanded"
          id="image-transcription-settings"
          class="vision-settings"
        >
          <div class="field-row">
            <div class="field">
              <label>Model</label>
              <input
                v-model="form.image_transcription.model"
                placeholder="gpt-4o-mini"
              >
            </div>
            <div class="field">
              <label>API Format</label>
              <select v-model="form.image_transcription.api_format">
                <option value="openai">
                  OpenAI Compatible
                </option>
                <option value="anthropic">
                  Anthropic
                </option>
              </select>
            </div>
          </div>
          <div class="field-row">
            <div class="field">
              <label>Base URL</label>
              <input
                v-model="form.image_transcription.base_url"
                placeholder="https://api.openai.com/v1"
              >
            </div>
            <div class="field">
              <label>API Key</label>
              <input
                v-model="form.image_transcription.api_key"
                type="password"
                :placeholder="form.image_transcription.api_key ? '•••••••• (unchanged)' : 'sk-...'"
              >
            </div>
          </div>
          <div class="field-row">
            <div class="field">
              <label>Max Tokens</label>
              <input
                v-model.number="form.image_transcription.max_tokens"
                type="number"
                min="1"
              >
            </div>
            <div class="field">
              <label>Temperature</label>
              <input
                v-model.number="form.image_transcription.temperature"
                type="number"
                step="0.1"
              >
            </div>
          </div>
          <div class="field">
            <label>Transcription Prompt</label>
            <textarea
              v-model="form.image_transcription.prompt"
              rows="4"
              placeholder="Describe the image for the main agent..."
            />
          </div>
        </div>
      </section>

      <!-- Agent Behavior -->
      <section class="form-section">
        <h3>Agent Behavior</h3>
        <div class="field">
          <label>Wake Words (comma separated)</label>
          <input
            v-model="form.wake_words"
            placeholder="atri, hey"
          >
        </div>
        <div class="field">
          <label>Persona</label>
          <textarea
            v-model="form.persona"
            rows="2"
            placeholder="Agent personality..."
          />
        </div>
        <div class="field">
          <label>Extra Instructions</label>
          <textarea
            v-model="form.extra_instructions"
            rows="3"
            placeholder="Additional system prompt..."
          />
        </div>
      </section>

      <!-- Web Search -->
      <section class="form-section">
        <h3>Web Search</h3>
        <p class="section-desc">
          Configure a Tavily API key for higher-quality web search. Leave empty to use DuckDuckGo (free).
        </p>
        <div class="field">
          <label>Tavily API Key</label>
          <input
            v-model="form.tavily_api_key"
            type="password"
            placeholder="tvly-..."
          >
        </div>
      </section>

      <!-- Music Library -->
      <section class="form-section">
        <h3>Music Library</h3>
        <p class="section-desc">
          Directories to scan for music files. Supports FLAC, MP3, WAV, M4A, OGG, AAC, and more.
        </p>
        <div class="music-dirs">
          <div
            v-for="(dir, i) in musicDirs"
            :key="i"
            class="music-dir-row"
          >
            <input
              v-model="musicDirs[i]"
              placeholder="C:\Music or /home/user/music"
              class="music-dir-input"
            >
            <button
              class="btn-icon-sm btn-danger"
              title="Remove"
              @click="musicDirs.splice(i, 1)"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              ><line
                x1="18"
                y1="6"
                x2="6"
                y2="18"
              /><line
                x1="6"
                y1="6"
                x2="18"
                y2="18"
              /></svg>
            </button>
          </div>
          <button
            class="btn btn-outline"
            @click="musicDirs.push('')"
          >
            + Add Directory
          </button>
        </div>
        <button
          class="btn btn-secondary"
          :disabled="savingMusic"
          style="margin-top:8px;"
          @click="saveMusicDirs"
        >
          {{ savingMusic ? 'Saving...' : 'Save Music Directories' }}
        </button>
      </section>

      <button
        class="btn btn-primary"
        :disabled="saving"
        @click="saveSettings"
      >
        {{ saving ? 'Saving...' : 'Save Settings' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
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
  image_transcription: {
    enabled: false,
    model: '',
    api_key: '',
    base_url: '',
    api_format: 'openai',
    prompt: '',
    max_tokens: 1024,
    temperature: 0,
  },
})

const saving = ref(false)
const savingMusic = ref(false)
const musicDirs = ref([''])
const imageTranscriptionExpanded = ref(false)

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
    form.value.image_transcription = normalizeImageTranscription(d.image_transcription)
  } catch {}
}

function normalizeImageTranscription(value = {}) {
  return {
    enabled: Boolean(value.enabled),
    model: value.model || '',
    api_key: value.api_key || '',
    base_url: value.base_url || '',
    api_format: value.api_format || 'openai',
    prompt: value.prompt || '',
    max_tokens: Number(value.max_tokens || 1024),
    temperature: Number(value.temperature || 0),
  }
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
  if (saving.value) return
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
      image_transcription: {
        ...form.value.image_transcription,
        model: form.value.image_transcription.model.trim(),
        base_url: form.value.image_transcription.base_url.trim(),
        api_key: form.value.image_transcription.api_key.trim(),
        prompt: form.value.image_transcription.prompt.trim(),
      },
    })
    await loadStatus()
  } finally {
    saving.value = false
  }
}

function handleSettingsShortcut(event) {
  const isSaveShortcut = (event.ctrlKey || event.metaKey) &&
    !event.shiftKey &&
    event.key.toLowerCase() === 's'
  if (!isSaveShortcut) return

  event.preventDefault()
  void saveSettings()
}

onMounted(async () => {
  window.addEventListener('keydown', handleSettingsShortcut)
  await Promise.all([loadProviders(), loadStatus(), loadSettings(), loadMusicDirs()])
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleSettingsShortcut)
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

.section-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.section-title-row h3 {
  margin-bottom: 4px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.collapse-button {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: -2px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
  transition: color 0.12s, background 0.12s, border-color 0.12s;
}

.collapse-button:hover,
.collapse-button:focus-visible {
  color: var(--t1);
  background: var(--bg2);
  border-color: var(--border);
  outline: none;
}

.collapse-button svg {
  width: 15px;
  height: 15px;
  transition: transform 0.12s;
}

.collapse-button.open svg {
  transform: rotate(90deg);
}

.vision-section {
  border-left: 2px solid rgba(55, 148, 255, 0.34);
  padding-left: 14px;
}

.vision-settings {
  padding-left: 32px;
}

.switch-field {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--t2);
  font-family: var(--mono);
  font-size: 11px;
  cursor: pointer;
  user-select: none;
}

.switch-track {
  width: 42px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg0);
  padding: 2px;
  transition: background 0.12s, border-color 0.12s;
}

.switch-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--t3);
  transition: transform 0.12s, background 0.12s;
}

.switch-field.active .switch-track {
  border-color: rgba(130, 184, 255, 0.45);
  background: var(--ok-bg);
}

.switch-field.active .switch-track .switch-thumb {
  background: var(--ok);
  transform: translateX(20px);
}

.switch-field:focus-visible .switch-track {
  outline: 1px solid var(--acc);
  outline-offset: 2px;
}

.switch-label {
  min-width: 48px;
  text-align: left;
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

@media (max-width: 720px) {
  .section-title-row,
  .field-row {
    flex-direction: column;
  }

  .vision-settings {
    padding-left: 0;
  }

  .switch-field {
    align-self: flex-start;
  }
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
  background: var(--acc-bg);
  color: var(--acc2);
  border-color: rgba(55, 148, 255, 0.3);
}

.btn-primary:hover {
  background: rgba(55, 148, 255, 0.22);
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
