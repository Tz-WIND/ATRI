<template>
  <div class="settings-page">
    <PageHeader title="Settings">
      <template #status>
        <span class="settings-subtitle">Interface, models, agent behavior</span>
      </template>
      <template #actions>
        <button
          class="header-save"
          :disabled="saving"
          @click="saveSettings"
        >
          {{ saving ? 'Saving' : 'Save Settings' }}
        </button>
      </template>
    </PageHeader>

    <div class="settings-shell">
      <nav
        class="settings-nav"
        aria-label="Settings sections"
      >
        <div class="settings-nav-head">
          <div class="settings-nav-title">
            Preferences
          </div>
          <div class="settings-nav-desc">
            Configuration is applied to the local dashboard runtime.
          </div>
        </div>

        <div class="settings-groups">
          <div
            v-for="group in settingsGroups"
            :key="group.label"
            class="settings-group"
          >
            <div class="settings-group-label">
              {{ group.label }}
            </div>
            <button
              v-for="tab in group.tabs"
              :key="tab.id"
              :class="['settings-tab', { active: activeTab === tab.id }]"
              type="button"
              @click="activeTab = tab.id"
            >
              <span
                class="settings-tab-icon"
                v-html="tab.icon"
              />
              <span class="settings-tab-label">{{ tab.label }}</span>
            </button>
          </div>
        </div>
      </nav>

      <main class="settings-main">
        <div class="settings-panel-head">
          <div class="settings-panel-title">
            {{ activeTabMeta.label }}
          </div>
          <div class="settings-panel-desc">
            {{ activeTabMeta.description }}
          </div>
        </div>

        <div class="settings-scroll">
          <section
            v-if="activeTab === 'providers'"
            class="settings-section"
          >
            <div class="section-heading-row">
              <div>
                <h3>Model Providers</h3>
                <p class="section-desc">
                  Manage API endpoints, credentials, and model discovery.
                </p>
              </div>
            </div>
            <ProviderWorkbench />
          </section>

          <section
            v-else-if="activeTab === 'models'"
            class="settings-section"
          >
            <div class="section-heading-row">
              <div>
                <h3>Active Models</h3>
                <p class="section-desc">
                  Models enabled for Chat. Switch between them from the composer.
                </p>
              </div>
            </div>
            <div class="settings-card">
              <ActiveModelsList />
            </div>
          </section>

          <section
            v-else-if="activeTab === 'generation'"
            class="settings-section"
          >
            <div class="section-heading-row">
              <div>
                <h3>Generation Parameters</h3>
                <p class="section-desc">
                  Defaults used by the agent when a model does not override them.
                </p>
              </div>
            </div>
            <div class="settings-card">
              <div class="setting-grid">
                <label class="setting-field">
                  <span>Max Tokens</span>
                  <input
                    v-model.number="form.max_tokens"
                    type="number"
                  >
                </label>
                <label class="setting-field">
                  <span>Temperature</span>
                  <input
                    v-model.number="form.temperature"
                    type="number"
                    step="0.1"
                  >
                </label>
                <label class="setting-field">
                  <span>Max Context Tokens</span>
                  <input
                    v-model.number="form.max_context_tokens"
                    type="number"
                  >
                </label>
                <label class="setting-field">
                  <span>Max Rounds</span>
                  <input
                    v-model.number="form.max_rounds"
                    type="number"
                  >
                </label>
              </div>
            </div>
          </section>

          <section
            v-else-if="activeTab === 'vision'"
            class="settings-section"
          >
            <div class="section-heading-row">
              <div>
                <h3>Image Transcription Model</h3>
                <p class="section-desc">
                  Route image attachments through a dedicated vision model before the main agent sees them.
                </p>
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
              </button>
            </div>

            <div class="settings-card">
              <div class="section-title-row">
                <div>
                  <div class="subsection-title">
                    Connection
                  </div>
                  <p class="section-desc compact">
                    Dedicated API settings for visual context extraction.
                  </p>
                </div>
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
              </div>

              <div
                v-show="imageTranscriptionExpanded"
                id="image-transcription-settings"
                class="vision-settings"
              >
                <div class="setting-grid">
                  <label class="setting-field">
                    <span>Model</span>
                    <input
                      v-model="form.image_transcription.model"
                      placeholder="gpt-4o-mini"
                    >
                  </label>
                  <label class="setting-field">
                    <span>API Format</span>
                    <select v-model="form.image_transcription.api_format">
                      <option value="openai">
                        OpenAI Compatible
                      </option>
                      <option value="anthropic">
                        Anthropic
                      </option>
                    </select>
                  </label>
                  <label class="setting-field">
                    <span>Base URL</span>
                    <input
                      v-model="form.image_transcription.base_url"
                      placeholder="https://api.openai.com/v1"
                    >
                  </label>
                  <label class="setting-field">
                    <span>API Key</span>
                    <input
                      v-model="form.image_transcription.api_key"
                      type="password"
                      :placeholder="form.image_transcription.api_key ? '•••••••• (unchanged)' : 'sk-...'"
                    >
                  </label>
                  <label class="setting-field">
                    <span>Max Tokens</span>
                    <input
                      v-model.number="form.image_transcription.max_tokens"
                      type="number"
                      min="1"
                    >
                  </label>
                  <label class="setting-field">
                    <span>Temperature</span>
                    <input
                      v-model.number="form.image_transcription.temperature"
                      type="number"
                      step="0.1"
                    >
                  </label>
                </div>
                <label class="setting-field full">
                  <span>Transcription Prompt</span>
                  <textarea
                    v-model="form.image_transcription.prompt"
                    rows="4"
                    placeholder="Describe the image for the main agent..."
                  />
                </label>
              </div>
            </div>
          </section>

          <section
            v-else-if="activeTab === 'agent'"
            class="settings-section"
          >
            <div class="section-heading-row">
              <div>
                <h3>Agent Behavior</h3>
                <p class="section-desc">
                  Wake words and persistent personality instructions.
                </p>
              </div>
            </div>
            <div class="settings-card">
              <label class="setting-field full">
                <span>Wake Words</span>
                <input
                  v-model="form.wake_words"
                  placeholder="atri, hey"
                >
              </label>
              <label class="setting-field full">
                <span>Persona</span>
                <textarea
                  v-model="form.persona"
                  rows="3"
                  placeholder="Agent personality..."
                />
              </label>
              <label class="setting-field full">
                <span>Extra Instructions</span>
                <textarea
                  v-model="form.extra_instructions"
                  rows="5"
                  placeholder="Additional system prompt..."
                />
              </label>
            </div>
          </section>

          <section
            v-else-if="activeTab === 'search'"
            class="settings-section"
          >
            <div class="section-heading-row">
              <div>
                <h3>Web Search</h3>
                <p class="section-desc">
                  Configure Tavily for higher-quality search, or leave empty to use DuckDuckGo.
                </p>
              </div>
            </div>
            <div class="settings-card">
              <label class="setting-field full">
                <span>Tavily API Key</span>
                <input
                  v-model="form.tavily_api_key"
                  type="password"
                  placeholder="tvly-..."
                >
              </label>
            </div>
          </section>

          <section
            v-else-if="activeTab === 'music'"
            class="settings-section"
          >
            <div class="section-heading-row">
              <div>
                <h3>Music Library</h3>
                <p class="section-desc">
                  Directories to scan for FLAC, MP3, WAV, M4A, OGG, AAC, and more.
                </p>
              </div>
              <button
                class="btn btn-secondary"
                :disabled="savingMusic"
                @click="saveMusicDirs"
              >
                {{ savingMusic ? 'Saving' : 'Save Directories' }}
              </button>
            </div>
            <div class="settings-card">
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
                  Add Directory
                </button>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import ProviderWorkbench from './ProviderWorkbench.vue'
import ActiveModelsList from './ActiveModelsList.vue'
import { useApi } from '@/composables/useApi.js'
import { useProviders } from '@/composables/useProviders.js'

const api = useApi()
const { loadProviders, loadStatus } = useProviders()

const icon = {
  server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v4m0 14v4M4.22 4.22l2.83 2.83m9.9 9.9l2.83 2.83M1 12h4m14 0h4M4.22 19.78l2.83-2.83m9.9-9.9l2.83-2.83"/></svg>',
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3m6-3v3M9 20v3m6-3v3M20 9h3m-3 6h3M1 9h3m-3 6h3"/></svg>',
  sliders: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 21v-7m0-4V3m8 18v-9m0-4V3m8 18v-5m0-4V3"/><path d="M1 14h6M9 8h6m2 8h6"/></svg>',
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>',
  agent: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2m16 0h2M9 13h.01M15 13h.01M9 17h6"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>',
  music: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
}

const settingsGroups = [
  {
    label: 'Core',
    tabs: [
      { id: 'providers', label: 'Providers', description: 'Endpoints, credentials, and model discovery.', icon: icon.server },
      { id: 'models', label: 'Models', description: 'Enabled chat models across providers.', icon: icon.cpu },
      { id: 'generation', label: 'Generation', description: 'Token, temperature, context, and round limits.', icon: icon.sliders },
    ],
  },
  {
    label: 'Agent',
    tabs: [
      { id: 'vision', label: 'Vision', description: 'Dedicated image transcription settings.', icon: icon.image },
      { id: 'agent', label: 'Behavior', description: 'Wake words, persona, and extra instructions.', icon: icon.agent },
      { id: 'search', label: 'Search', description: 'Web search provider credentials.', icon: icon.search },
    ],
  },
  {
    label: 'Library',
    tabs: [
      { id: 'music', label: 'Music', description: 'Directories scanned by the music library.', icon: icon.music },
    ],
  },
]

const flatTabs = settingsGroups.flatMap(group => group.tabs)
const activeTab = ref('providers')
const activeTabMeta = computed(() => flatTabs.find(tab => tab.id === activeTab.value) || flatTabs[0])

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
    imageTranscriptionExpanded.value = Boolean(form.value.image_transcription.enabled)
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

.settings-subtitle {
  color: var(--t3);
  font-size: 12px;
}

.header-save {
  min-height: 30px;
  padding: 0 13px;
  border: 1px solid rgba(125, 168, 232, 0.28);
  border-radius: var(--radius-sm);
  background: var(--acc-bg);
  color: var(--acc2);
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, opacity 0.15s;
}

.header-save:hover:not(:disabled) {
  background: var(--acc-bg-strong);
  border-color: rgba(125, 168, 232, 0.4);
}

.header-save:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.settings-shell {
  flex: 1;
  min-height: 0;
  display: flex;
}

.settings-nav {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  background: rgba(24, 24, 24, 0.42);
  padding: 16px 10px;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.settings-nav-head {
  padding: 0 8px 14px;
}

.settings-nav-title {
  color: var(--t1);
  font-size: 14px;
  font-weight: 650;
}

.settings-nav-desc {
  color: var(--t3);
  font-size: 11px;
  line-height: 1.45;
  margin-top: 3px;
}

.settings-groups {
  display: flex;
  flex-direction: column;
  gap: 13px;
}

.settings-group-label {
  padding: 0 9px 5px;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.settings-tab {
  width: 100%;
  min-height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.settings-tab:hover {
  background: var(--bg-050);
  color: var(--t2);
}

.settings-tab.active {
  background: var(--bg-100);
  border-color: var(--border-strong);
  color: var(--t1);
}

.settings-tab-icon {
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.settings-tab-icon :deep(svg) {
  width: 16px;
  height: 16px;
}

.settings-tab-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
}

.settings-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.settings-panel-head {
  min-height: 66px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 12px 24px;
  border-bottom: 1px solid var(--border);
  background: rgba(24, 24, 24, 0.38);
}

.settings-panel-title {
  color: var(--t1);
  font-size: 18px;
  font-weight: 650;
}

.settings-panel-desc {
  color: var(--t3);
  font-size: 12px;
  margin-top: 2px;
}

.settings-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 24px 26px;
}

.settings-section {
  max-width: 980px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.section-heading-row,
.section-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.settings-section h3 {
  color: var(--t1);
  font-size: 14px;
  font-weight: 650;
  letter-spacing: 0;
}

.section-desc {
  color: var(--t3);
  font-size: 12px;
  line-height: 1.55;
  margin-top: 3px;
}

.section-desc.compact {
  margin-top: 2px;
  margin-bottom: 0;
}

.subsection-title {
  color: var(--t1);
  font-size: 13px;
  font-weight: 650;
}

.settings-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.032);
  padding: 15px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
}

.setting-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.setting-field {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--t2);
  font-size: 12px;
  font-weight: 600;
}

.setting-field.full {
  margin-top: 12px;
}

.setting-field.full:first-child {
  margin-top: 0;
}

.setting-field span {
  color: var(--t2);
  font-size: 12px;
}

.setting-field input,
.setting-field select,
.setting-field textarea,
.music-dir-input {
  width: 100%;
  border: 1px solid var(--border-input);
  border-radius: 7px;
  background: rgba(24, 24, 24, 0.62);
  color: var(--t1);
  padding: 8px 10px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}

.setting-field input,
.setting-field select,
.music-dir-input {
  height: 36px;
}

.setting-field textarea {
  resize: vertical;
  line-height: 1.5;
}

.setting-field input:focus,
.setting-field select:focus,
.setting-field textarea:focus,
.music-dir-input:focus {
  border-color: rgba(158, 191, 255, 0.5);
  background: rgba(24, 24, 24, 0.82);
  box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12);
}

.collapse-button,
.btn-icon-sm {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
  transition: color 0.15s, background 0.15s, border-color 0.15s;
}

.collapse-button:hover,
.collapse-button:focus-visible,
.btn-icon-sm:hover {
  color: var(--t1);
  background: var(--bg-100);
  border-color: var(--border-light);
}

.collapse-button svg,
.btn-icon-sm svg {
  width: 15px;
  height: 15px;
}

.collapse-button svg {
  transition: transform 0.15s;
}

.collapse-button.open svg {
  transform: rotate(90deg);
}

.vision-settings {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.switch-field {
  width: 38px;
  height: 22px;
  flex-shrink: 0;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.switch-track {
  width: 38px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border-input);
  border-radius: 999px;
  background: var(--bg3);
  padding: 2px;
  transition: background 0.15s, border-color 0.15s;
}

.switch-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.15s;
}

.switch-field.active .switch-track {
  border-color: rgba(158, 191, 255, 0.5);
  background: var(--acc);
}

.switch-field.active .switch-thumb {
  transform: translateX(16px);
}

.music-dirs {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.music-dir-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.music-dir-input {
  flex: 1;
  font-family: var(--mono);
}

.btn {
  min-height: 32px;
  padding: 0 13px;
  border-radius: 7px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  font-weight: 650;
  transition: background 0.15s, border-color 0.15s, color 0.15s, opacity 0.15s;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  background: var(--acc-bg);
  color: var(--acc2);
  border-color: rgba(125, 168, 232, 0.24);
}

.btn-secondary:hover:not(:disabled) {
  background: var(--acc-bg-strong);
  border-color: rgba(125, 168, 232, 0.38);
}

.btn-outline {
  align-self: flex-start;
  background: transparent;
  color: var(--t2);
}

.btn-outline:hover {
  background: var(--bg-100);
  color: var(--t1);
}

.btn-danger:hover {
  color: var(--red);
  background: var(--red-bg);
  border-color: rgba(255, 141, 127, 0.26);
}

@media (max-width: 920px) {
  .settings-shell {
    flex-direction: column;
  }

  .settings-nav {
    width: 100%;
    max-height: 188px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .settings-nav-desc {
    display: none;
  }

  .settings-groups {
    gap: 10px;
  }

  .settings-group {
    min-width: 0;
  }

  .settings-group .settings-tab {
    display: inline-flex;
    width: auto;
    margin-right: 6px;
  }

  .setting-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .settings-subtitle {
    display: none;
  }

  .settings-scroll,
  .settings-panel-head {
    padding-left: 16px;
    padding-right: 16px;
  }

  .section-heading-row,
  .section-title-row {
    flex-direction: column;
  }
}
</style>
