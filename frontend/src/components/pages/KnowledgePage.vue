<template>
  <div class="page">
    <PageHeader title="Knowledge">
      <template #status>
        <StatusBadge
          type="info"
          dot
        >
          {{ bases.length }} bases
        </StatusBadge>
      </template>
      <template #actions>
        <button
          class="btn btn-ghost"
          :disabled="loading"
          @click="loadBases"
        >
          {{ loading ? 'Loading' : 'Reload' }}
        </button>
        <button
          class="btn btn-primary"
          @click="toggleCreate"
        >
          {{ showCreate ? 'Cancel' : 'New Base' }}
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <div
        v-if="error"
        class="notice error"
      >
        {{ error }}
      </div>

      <div class="summary-strip">
        <div class="metric">
          <span class="metric-value">{{ summary.docs }}</span>
          <span class="metric-label">documents</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ summary.chunks }}</span>
          <span class="metric-label">chunks</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ activeEmbeddingModels.length }}</span>
          <span class="metric-label">embedding models</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ activeRerankModels.length }}</span>
          <span class="metric-label">rerank models</span>
        </div>
      </div>

      <div
        v-if="showCreate"
        class="card create-card"
      >
        <section class="form-section">
          <h3>Create Knowledge Base</h3>
          <div class="form-grid">
            <div class="field">
              <label>Name</label>
              <input
                v-model.trim="form.name"
                placeholder="project-docs"
                spellcheck="false"
              >
            </div>
            <div class="field">
              <label>Embedding</label>
              <select v-model="form.embeddingKey">
                <option value="">
                  Select embedding model
                </option>
                <option
                  v-for="model in embeddingOptions"
                  :key="model.key"
                  :value="model.key"
                >
                  {{ model.label }}
                </option>
              </select>
            </div>
            <div class="field">
              <label>Rerank</label>
              <select v-model="form.rerankKey">
                <option value="">
                  None
                </option>
                <option
                  v-for="model in rerankOptions"
                  :key="model.key"
                  :value="model.key"
                >
                  {{ model.label }}
                </option>
              </select>
            </div>
            <div class="field wide">
              <label>Description</label>
              <input
                v-model.trim="form.description"
                placeholder="Internal docs and notes"
                spellcheck="false"
              >
            </div>
          </div>
          <div class="number-grid">
            <div class="field">
              <label>Chunk Size</label>
              <input
                v-model.number="form.chunk_size"
                type="number"
                min="100"
                max="4000"
              >
            </div>
            <div class="field">
              <label>Overlap</label>
              <input
                v-model.number="form.chunk_overlap"
                type="number"
                min="0"
                max="1000"
              >
            </div>
            <div class="field">
              <label>Dense Top K</label>
              <input
                v-model.number="form.top_k_dense"
                type="number"
                min="1"
                max="200"
              >
            </div>
            <div class="field">
              <label>Sparse Top K</label>
              <input
                v-model.number="form.top_k_sparse"
                type="number"
                min="1"
                max="200"
              >
            </div>
            <div class="field">
              <label>Final Top M</label>
              <input
                v-model.number="form.top_m_final"
                type="number"
                min="1"
                max="50"
              >
            </div>
          </div>
          <div class="form-actions">
            <button
              class="btn btn-primary"
              :disabled="saving"
              @click="createBase"
            >
              {{ saving ? 'Creating' : 'Create' }}
            </button>
            <button
              class="btn btn-ghost"
              @click="showCreate = false"
            >
              Cancel
            </button>
          </div>
        </section>
      </div>

      <div class="knowledge-layout">
        <aside class="kb-sidebar">
          <div
            v-if="!loading && bases.length === 0"
            class="empty"
          >
            No knowledge bases.
          </div>
          <button
            v-for="base in bases"
            :key="base.kb_id"
            :class="['base-item', { active: selectedKbId === base.kb_id }]"
            @click="selectBase(base.kb_id)"
          >
            <span class="base-name">{{ base.name }}</span>
            <span class="base-meta">{{ base.doc_count || 0 }} docs / {{ base.chunk_count || 0 }} chunks</span>
          </button>
        </aside>

        <main class="kb-main">
          <template v-if="selectedKb">
            <section class="card detail-card">
              <div class="card-header">
                <div class="title-stack">
                  <span class="card-title">{{ selectedKb.name }}</span>
                  <span class="card-meta">{{ selectedKb.description || selectedKb.kb_id }}</span>
                </div>
                <StatusBadge :type="selectedKb.rerank_model ? 'on' : 'default'">
                  {{ selectedKb.rerank_model ? 'rerank' : 'vector only' }}
                </StatusBadge>
              </div>
              <div class="inventory">
                <span>{{ selectedKb.embedding_provider }}/{{ selectedKb.embedding_model }}</span>
                <span v-if="selectedKb.rerank_model">{{ selectedKb.rerank_provider }}/{{ selectedKb.rerank_model }}</span>
                <span>{{ selectedKb.chunk_size }} / {{ selectedKb.chunk_overlap }}</span>
                <span>{{ selectedKb.top_k_dense }} dense</span>
                <span>{{ selectedKb.top_k_sparse }} sparse</span>
                <span>{{ selectedKb.top_m_final }} final</span>
              </div>
              <div class="context-controls">
                <button
                  :class="['btn', isSelectedKbActive ? 'btn-ghost' : 'btn-primary']"
                  :disabled="contextSaving"
                  @click="toggleSelectedBaseForChat"
                >
                  {{ isSelectedKbActive ? 'Remove from Chat' : 'Use in Chat' }}
                </button>
                <label class="inline-field">
                  <span>Context Top K</span>
                  <input
                    v-model.number="knowledgeConfig.top_k"
                    type="number"
                    min="1"
                    max="20"
                  >
                </label>
                <button
                  class="btn btn-ghost"
                  :disabled="contextSaving"
                  @click="saveKnowledgeContext"
                >
                  {{ contextSaving ? 'Saving' : 'Save Context' }}
                </button>
                <StatusBadge :type="isSelectedKbActive ? 'on' : 'default'">
                  {{ isSelectedKbActive ? 'chat context on' : 'chat context off' }}
                </StatusBadge>
              </div>
              <div class="card-actions">
                <button
                  class="btn btn-danger"
                  @click="removeBase"
                >
                  Delete Base
                </button>
              </div>
            </section>

            <section class="card import-card">
              <div class="card-header">
                <span class="card-title">Documents</span>
                <div class="inline-actions">
                  <input
                    ref="fileInput"
                    type="file"
                    class="file-input"
                    accept=".txt,.md,.json,.csv,.log"
                    @change="onFileSelected"
                  >
                  <button
                    class="btn btn-ghost"
                    :disabled="uploading"
                    @click="triggerUpload"
                  >
                    {{ uploading ? 'Uploading' : 'Upload File' }}
                  </button>
                </div>
              </div>

              <div class="import-grid">
                <div class="field">
                  <label>File Name</label>
                  <input
                    v-model.trim="importForm.file_name"
                    placeholder="notes.txt"
                    spellcheck="false"
                  >
                </div>
                <div class="field">
                  <label>Source</label>
                  <input
                    v-model.trim="importForm.source"
                    placeholder="import"
                    spellcheck="false"
                  >
                </div>
                <div class="field wide">
                  <label>Content</label>
                  <textarea
                    v-model="importForm.content"
                    rows="5"
                    spellcheck="false"
                  />
                </div>
              </div>
              <div class="form-actions">
                <button
                  class="btn btn-primary"
                  :disabled="importing"
                  @click="importDocument"
                >
                  {{ importing ? 'Importing' : 'Import Text' }}
                </button>
              </div>

              <div
                v-if="taskStatus"
                :class="['notice', taskStatus.status === 'failed' ? 'error' : 'ok', 'compact']"
              >
                {{ taskStatus.kind }} {{ taskStatus.status }}{{ taskStatus.error ? `: ${taskStatus.error}` : '' }}
              </div>

              <div
                v-if="documents.length === 0"
                class="empty compact-empty"
              >
                No documents.
              </div>
              <div class="rows">
                <div
                  v-for="doc in documents"
                  :key="doc.doc_id"
                  :class="['row document-row', { active: selectedDocId === doc.doc_id }]"
                >
                  <button
                    class="row-main"
                    @click="selectDocument(doc.doc_id)"
                  >
                    <span class="row-name">{{ doc.doc_name }}</span>
                    <span class="row-meta">{{ doc.file_type }} / {{ doc.chunk_count || 0 }} chunks / {{ formatBytes(doc.file_size) }}</span>
                  </button>
                  <button
                    class="btn btn-danger"
                    @click="removeDocument(doc.doc_id)"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </section>

            <section class="card chunks-card">
              <div class="card-header">
                <span class="card-title">Chunks</span>
                <StatusBadge type="default">
                  {{ chunks.length }} loaded
                </StatusBadge>
              </div>
              <div
                v-if="!selectedDocId"
                class="empty"
              >
                Select a document.
              </div>
              <div class="chunk-list">
                <article
                  v-for="chunk in chunks"
                  :key="chunk.chunk_id"
                  class="chunk-item"
                >
                  <div class="chunk-head">
                    <span>#{{ chunk.chunk_index }}</span>
                    <span>{{ chunk.char_count }} chars</span>
                    <button
                      class="btn btn-danger mini"
                      @click="removeChunk(chunk.chunk_id)"
                    >
                      Delete
                    </button>
                  </div>
                  <p>{{ chunk.content }}</p>
                </article>
              </div>
            </section>
          </template>

          <div
            v-else
            class="empty empty-panel"
          >
            Select or create a knowledge base.
          </div>
        </main>

        <aside class="retrieve-panel card">
          <div class="card-header">
            <span class="card-title">Retrieve</span>
            <StatusBadge type="info">
              top {{ retrieveForm.top_k }}
            </StatusBadge>
          </div>
          <div class="field">
            <label>Query</label>
            <textarea
              v-model.trim="retrieveForm.query"
              rows="4"
              spellcheck="false"
            />
          </div>
          <div class="field">
            <label>Top K</label>
            <input
              v-model.number="retrieveForm.top_k"
              type="number"
              min="1"
              max="20"
            >
          </div>
          <button
            class="btn btn-primary block"
            :disabled="retrieving || !selectedKb"
            @click="retrieve"
          >
            {{ retrieving ? 'Retrieving' : 'Run' }}
          </button>

          <div class="result-list">
            <article
              v-for="item in retrievalResults"
              :key="item.chunk_id"
              class="result-item"
            >
              <div class="result-head">
                <span>{{ item.kb_name }} / {{ item.doc_name }} #{{ item.chunk_index }}</span>
                <span>{{ scoreLabel(item) }}</span>
              </div>
              <p>{{ item.content }}</p>
            </article>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useApi } from '@/composables/useApi.js'
import { useProviders } from '@/composables/useProviders.js'

const api = useApi()
const {
  activeEmbeddingModels,
  activeRerankModels,
  loadStatus,
} = useProviders()

const bases = ref([])
const documents = ref([])
const chunks = ref([])
const selectedKbId = ref('')
const selectedDocId = ref('')
const loading = ref(false)
const saving = ref(false)
const importing = ref(false)
const uploading = ref(false)
const retrieving = ref(false)
const contextSaving = ref(false)
const showCreate = ref(false)
const error = ref('')
const taskStatus = ref(null)
const retrievalResults = ref([])
const fileInput = ref(null)
const knowledgeConfig = ref({
  enabled: false,
  active_bases: [],
  top_k: 5,
})

const blankForm = () => ({
  name: '',
  description: '',
  embeddingKey: '',
  rerankKey: '',
  chunk_size: 800,
  chunk_overlap: 120,
  top_k_dense: 30,
  top_k_sparse: 30,
  top_m_final: 5,
})

const form = ref(blankForm())
const importForm = ref({
  file_name: 'document.txt',
  content: '',
  source: 'import',
})
const retrieveForm = ref({
  query: '',
  top_k: 5,
})

const selectedKb = computed(() => bases.value.find(base => base.kb_id === selectedKbId.value) || null)
const selectedDoc = computed(() => documents.value.find(doc => doc.doc_id === selectedDocId.value) || null)
const isSelectedKbActive = computed(() => {
  if (!selectedKb.value || !knowledgeConfig.value.enabled) return false
  return knowledgeConfig.value.active_bases.includes(selectedKb.value.kb_id)
})

const summary = computed(() => ({
  docs: bases.value.reduce((sum, base) => sum + (base.doc_count || 0), 0),
  chunks: bases.value.reduce((sum, base) => sum + (base.chunk_count || 0), 0),
}))

const embeddingOptions = computed(() => activeEmbeddingModels.value.map(modelOption))
const rerankOptions = computed(() => activeRerankModels.value.map(modelOption))

onMounted(async () => {
  await loadStatus()
  setDefaultModelKeys()
  await loadKnowledgeContext()
  await loadBases()
})

async function loadKnowledgeContext() {
  try {
    const settings = await api.getSettings()
    knowledgeConfig.value = normalizeKnowledge(settings.knowledge)
  } catch {
    knowledgeConfig.value = normalizeKnowledge({})
  }
}

async function loadBases() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.getKnowledgeBases()
    bases.value = data.items || []
    if (!selectedKbId.value && bases.value.length) {
      selectedKbId.value = bases.value[0].kb_id
    }
    if (selectedKbId.value && !bases.value.some(base => base.kb_id === selectedKbId.value)) {
      selectedKbId.value = bases.value[0]?.kb_id || ''
    }
    await loadDocuments()
  } catch (err) {
    bases.value = []
    documents.value = []
    chunks.value = []
    error.value = err.message || 'failed to load knowledge bases'
  } finally {
    loading.value = false
  }
}

function toggleCreate() {
  showCreate.value = !showCreate.value
  if (showCreate.value) setDefaultModelKeys()
}

async function createBase() {
  const name = form.value.name.trim()
  if (!name) return setError('knowledge base name is required')
  const embedding = parseModelKey(form.value.embeddingKey)
  if (!embedding) return setError('embedding model is required')
  const rerank = parseModelKey(form.value.rerankKey)
  saving.value = true
  error.value = ''
  try {
    const created = await api.createKnowledgeBase({
      name,
      description: form.value.description,
      embedding_provider: embedding.provider,
      embedding_model: embedding.model,
      rerank_provider: rerank?.provider || '',
      rerank_model: rerank?.model || '',
      chunk_size: numberValue(form.value.chunk_size, 800),
      chunk_overlap: numberValue(form.value.chunk_overlap, 120),
      top_k_dense: numberValue(form.value.top_k_dense, 30),
      top_k_sparse: numberValue(form.value.top_k_sparse, 30),
      top_m_final: numberValue(form.value.top_m_final, 5),
    })
    selectedKbId.value = created.kb_id
    form.value = blankForm()
    setDefaultModelKeys()
    showCreate.value = false
    await loadBases()
  } catch (err) {
    error.value = err.message || 'failed to create knowledge base'
  } finally {
    saving.value = false
  }
}

async function selectBase(kbId) {
  if (selectedKbId.value === kbId) return
  selectedKbId.value = kbId
  selectedDocId.value = ''
  chunks.value = []
  retrievalResults.value = []
  await loadDocuments()
}

async function removeBase() {
  if (!selectedKb.value) return
  if (!confirm(`Delete knowledge base "${selectedKb.value.name}"?`)) return
  try {
    await api.deleteKnowledgeBase(selectedKb.value.kb_id)
    if (knowledgeConfig.value.active_bases.includes(selectedKb.value.kb_id)) {
      const active_bases = knowledgeConfig.value.active_bases.filter(id => id !== selectedKb.value.kb_id)
      await saveKnowledgeContext({ ...knowledgeConfig.value, active_bases })
    }
    selectedKbId.value = ''
    selectedDocId.value = ''
    retrievalResults.value = []
    await loadBases()
  } catch (err) {
    error.value = err.message || 'failed to delete knowledge base'
  }
}

async function loadDocuments() {
  documents.value = []
  chunks.value = []
  selectedDocId.value = ''
  if (!selectedKbId.value) return
  try {
    const data = await api.getKnowledgeDocuments(selectedKbId.value)
    documents.value = data.items || []
  } catch (err) {
    error.value = err.message || 'failed to load documents'
  }
}

async function importDocument() {
  if (!selectedKb.value) return
  if (!importForm.value.content.trim()) return setError('document content is required')
  importing.value = true
  error.value = ''
  try {
    const task = await api.importKnowledgeDocument(selectedKb.value.kb_id, {
      file_name: importForm.value.file_name || 'document.txt',
      content: importForm.value.content,
      source: importForm.value.source || 'import',
    })
    taskStatus.value = task
    await refreshTask(task.task_id)
    importForm.value.content = ''
    await loadBases()
  } catch (err) {
    error.value = err.message || 'failed to import document'
  } finally {
    importing.value = false
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

async function onFileSelected(event) {
  const file = event.target.files?.[0]
  if (!file || !selectedKb.value) return
  uploading.value = true
  error.value = ''
  try {
    const task = await api.uploadKnowledgeDocument(selectedKb.value.kb_id, file)
    taskStatus.value = task
    await refreshTask(task.task_id)
    await loadBases()
  } catch (err) {
    error.value = err.message || 'failed to upload document'
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

async function removeDocument(docId) {
  const doc = documents.value.find(item => item.doc_id === docId)
  if (doc && !confirm(`Delete document "${doc.doc_name}"?`)) return
  try {
    await api.deleteKnowledgeDocument(docId)
    if (selectedDocId.value === docId) {
      selectedDocId.value = ''
      chunks.value = []
    }
    await loadBases()
  } catch (err) {
    error.value = err.message || 'failed to delete document'
  }
}

async function selectDocument(docId) {
  if (selectedDocId.value === docId) {
    selectedDocId.value = ''
    chunks.value = []
    return
  }
  selectedDocId.value = docId
  await loadChunks()
}

async function loadChunks() {
  chunks.value = []
  if (!selectedDoc.value) return
  try {
    const data = await api.getKnowledgeChunks(selectedDoc.value.doc_id, 1, 100)
    chunks.value = data.items || []
  } catch (err) {
    error.value = err.message || 'failed to load chunks'
  }
}

async function removeChunk(chunkId) {
  if (!confirm('Delete chunk?')) return
  try {
    await api.deleteKnowledgeChunk(chunkId)
    await loadChunks()
    await loadBases()
  } catch (err) {
    error.value = err.message || 'failed to delete chunk'
  }
}

async function retrieve() {
  if (!selectedKb.value || !retrieveForm.value.query.trim()) return
  retrieving.value = true
  error.value = ''
  try {
    const data = await api.retrieveKnowledge({
      query: retrieveForm.value.query,
      kb_ids: [selectedKb.value.kb_id],
      top_k: numberValue(retrieveForm.value.top_k, 5),
    })
    retrievalResults.value = data.results || []
  } catch (err) {
    retrievalResults.value = []
    error.value = err.message || 'failed to retrieve knowledge'
  } finally {
    retrieving.value = false
  }
}

async function toggleSelectedBaseForChat() {
  if (!selectedKb.value) return
  const current = normalizeKnowledge(knowledgeConfig.value)
  const kbId = selectedKb.value.kb_id
  const active_bases = isSelectedKbActive.value
    ? current.active_bases.filter(id => id !== kbId)
    : Array.from(new Set([...current.active_bases, kbId]))
  await saveKnowledgeContext({
    ...current,
    enabled: active_bases.length > 0,
    active_bases,
  })
}

async function saveKnowledgeContext(nextConfig = knowledgeConfig.value) {
  contextSaving.value = true
  error.value = ''
  const next = normalizeKnowledge(nextConfig)
  next.enabled = next.active_bases.length > 0 && next.enabled
  try {
    await api.saveSettings({ knowledge: next })
    knowledgeConfig.value = next
  } catch (err) {
    error.value = err.message || 'failed to save knowledge chat context'
  } finally {
    contextSaving.value = false
  }
}

async function refreshTask(taskId) {
  if (!taskId) return
  try {
    taskStatus.value = await api.getKnowledgeTask(taskId)
  } catch {}
}

function modelOption(model) {
  const provider = model.provider || ''
  const name = model.model || ''
  return {
    key: `${provider}::${name}`,
    provider,
    model: name,
    label: `${provider}/${name}`,
  }
}

function parseModelKey(key) {
  if (!key || !key.includes('::')) return null
  const [provider, model] = key.split('::')
  if (!provider || !model) return null
  return { provider, model }
}

function setDefaultModelKeys() {
  if (!form.value.embeddingKey && embeddingOptions.value.length) {
    form.value.embeddingKey = embeddingOptions.value[0].key
  }
  if (!form.value.rerankKey && rerankOptions.value.length) {
    form.value.rerankKey = ''
  }
}

function normalizeKnowledge(value = {}) {
  const activeBases = Array.isArray(value.active_bases) ? value.active_bases : []
  return {
    enabled: Boolean(value.enabled),
    active_bases: activeBases.map(item => String(item).trim()).filter(Boolean),
    top_k: numberValue(value.top_k, 5),
  }
}

function numberValue(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function setError(message) {
  error.value = message
}

function formatBytes(value) {
  const bytes = Number(value) || 0
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function scoreLabel(item) {
  const score = item.score ?? item.final_score ?? item.dense_score ?? 0
  return Number(score).toFixed(3)
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.page-body { flex: 1; overflow-y: auto; padding: 22px 24px; min-width: 0; }

.summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.metric {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.032);
}

.metric-value {
  display: block;
  color: var(--t1);
  font-size: 18px;
  font-weight: 700;
  font-family: var(--mono);
  line-height: 1.1;
}

.metric-label {
  color: var(--t3);
  font-size: 11px;
  font-family: var(--mono);
}

.knowledge-layout {
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr) 330px;
  gap: 12px;
  align-items: start;
}

.kb-sidebar {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.base-item {
  width: 100%;
  text-align: left;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.032);
  color: var(--t2);
  padding: 10px 11px;
  cursor: pointer;
  transition: all 0.12s;
}

.base-item:hover,
.base-item.active {
  color: var(--t1);
  background: var(--bg-100);
  border-color: var(--border-strong);
}

.base-name,
.base-meta {
  display: block;
  font-family: var(--mono);
  overflow-wrap: anywhere;
}

.base-name { font-size: 12px; font-weight: 700; }
.base-meta { margin-top: 4px; font-size: 10px; color: var(--t3); }

.kb-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.card {
  background: rgba(255, 255, 255, 0.032);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
}

.create-card { margin-bottom: 14px; }
.form-section h3 {
  font-size: 14px;
  letter-spacing: 0;
  color: var(--t1);
  margin-bottom: 12px;
  font-weight: 650;
}

.form-grid,
.import-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.number-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.wide { grid-column: 1 / -1; }

.field { margin-bottom: 12px; min-width: 0; }
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
  background: rgba(24, 24, 24, 0.66);
  border: 1px solid var(--border-input);
  border-radius: 7px;
  color: var(--t1);
  padding: 8px 12px;
  font-size: 13px;
  font-family: var(--mono);
  outline: none;
  resize: vertical;
}

.field input:focus,
.field select:focus,
.field textarea:focus {
  border-color: rgba(158, 191, 255, 0.5);
  box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}

.title-stack { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.card-title {
  font-size: 13px;
  font-weight: 700;
  font-family: var(--mono);
  color: var(--t1);
}

.card-meta {
  font-size: 11px;
  color: var(--t3);
  font-family: var(--mono);
  overflow-wrap: anywhere;
}

.inventory {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--t3);
  font-size: 11px;
  font-family: var(--mono);
}

.inventory span {
  background: rgba(24, 24, 24, 0.54);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 8px;
  overflow-wrap: anywhere;
}

.context-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.inline-field {
  min-width: 150px;
  display: inline-grid;
  grid-template-columns: auto 72px;
  align-items: center;
  gap: 8px;
  color: var(--t2);
  font-family: var(--mono);
  font-size: 11px;
}

.inline-field input {
  width: 72px;
  height: 30px;
  background: rgba(24, 24, 24, 0.66);
  border: 1px solid var(--border-input);
  border-radius: 7px;
  color: var(--t1);
  padding: 5px 8px;
  font-size: 12px;
  font-family: var(--mono);
  outline: none;
}

.rows { display: flex; flex-direction: column; gap: 6px; }
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 8px;
  background: rgba(24, 24, 24, 0.42);
}

.row.active { border-color: rgba(158, 191, 255, 0.38); }
.row-main {
  min-width: 0;
  text-align: left;
  border: 0;
  background: none;
  color: inherit;
  cursor: pointer;
}

.row-name,
.row-meta {
  display: block;
  font-family: var(--mono);
  overflow-wrap: anywhere;
}

.row-name { color: var(--t1); font-size: 12px; }
.row-meta { color: var(--t3); font-size: 10px; margin-top: 3px; }

.chunk-list,
.result-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chunk-list {
  max-height: min(58vh, 720px);
  overflow-y: auto;
  padding-right: 4px;
}

.chunk-item,
.result-item {
  border: 1px solid var(--border);
  border-radius: 7px;
  background: rgba(24, 24, 24, 0.42);
  padding: 9px 10px;
}

.chunk-head,
.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 10px;
  margin-bottom: 6px;
}

.chunk-item p,
.result-item p {
  margin: 0;
  color: var(--t2);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.retrieve-panel {
  position: sticky;
  top: 0;
  min-width: 0;
}

.inline-actions,
.form-actions,
.card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.card-actions { margin-top: 12px; }
.file-input { display: none; }
.block { width: 100%; justify-content: center; }

.btn {
  padding: 6px 14px;
  border-radius: 7px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  font-family: var(--mono);
  transition: all 0.12s;
}

.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn-primary { background: var(--acc-bg); color: var(--acc2); border-color: rgba(125,168,232,0.3); }
.btn-primary:hover:not(:disabled) { background: var(--acc-bg-strong); }
.btn-ghost { background: none; color: var(--t2); }
.btn-ghost:hover:not(:disabled) { background: var(--bg-100); color: var(--t1); }
.btn-danger { background: none; color: var(--red); border-color: rgba(255,141,127,0.25); }
.btn-danger:hover { background: var(--red-bg); }
.mini { padding: 3px 8px; font-size: 10px; }

.notice {
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 9px 11px;
  margin-bottom: 12px;
  font-size: 12px;
  font-family: var(--mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.notice.compact { margin-top: 10px; margin-bottom: 10px; }
.notice.error {
  color: var(--red);
  background: var(--red-bg);
  border-color: rgba(255,141,127,0.28);
}

.notice.ok {
  color: var(--ok);
  background: var(--ok-bg);
  border-color: rgba(130,184,255,0.28);
}

.empty { color: var(--t3); font-size: 13px; }
.compact-empty { margin: 12px 0 4px; }
.empty-panel {
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 28px;
  text-align: center;
}

@media (max-width: 1180px) {
  .knowledge-layout { grid-template-columns: 220px minmax(0, 1fr); }
  .retrieve-panel { grid-column: 1 / -1; position: static; }
}

@media (max-width: 820px) {
  .summary-strip,
  .form-grid,
  .number-grid,
  .import-grid,
  .knowledge-layout {
    grid-template-columns: 1fr;
  }
  .card-header,
  .chunk-head,
  .result-head {
    flex-direction: column;
    align-items: flex-start;
  }
  .row { grid-template-columns: 1fr; }
}
</style>
