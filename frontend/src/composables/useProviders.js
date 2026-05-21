import { ref, computed } from 'vue'
import { useApi } from './useApi.js'

let instance = null

export function useProviders() {
  if (instance) return instance

  const api = useApi()
  const providers = ref({})
  const selectedName = ref('')
  const activeModels = ref([])
  const activeEmbeddingModels = ref([])
  const activeRerankModels = ref([])
  const activeModel = ref('')
  const activeModelProvider = ref('')
  const activeEmbeddingModel = ref('')
  const activeEmbeddingProvider = ref('')
  const activeRerankModel = ref('')
  const activeRerankProvider = ref('')
  const loading = ref(false)

  const selectedProvider = computed(() => {
    return selectedName.value ? providers.value[selectedName.value] : null
  })

  const providerList = computed(() => {
    return Object.entries(providers.value).map(([name, cfg]) => ({ name, ...cfg }))
  })

  async function loadProviders() {
    try {
      providers.value = await api.getProviders()
    } catch {
      providers.value = {}
    }
  }

  async function loadStatus() {
    try {
      const s = await api.getStatus()
      activeModel.value = s.model || ''
      activeModelProvider.value = s.model_provider || ''
      activeModels.value = s.active_models || []
      activeEmbeddingModel.value = s.embedding_model || ''
      activeEmbeddingProvider.value = s.embedding_provider || ''
      activeEmbeddingModels.value = s.active_embedding_models || []
      activeRerankModel.value = s.rerank_model || ''
      activeRerankProvider.value = s.rerank_provider || ''
      activeRerankModels.value = s.active_rerank_models || []
    } catch {}
  }

  function isModelActive(provider, model) {
    return activeModel.value === model && activeModelProvider.value === provider
  }

  function poolModels(pool) {
    if (pool === 'chat') return activeModels.value
    return pool === 'embedding' ? activeEmbeddingModels.value : activeRerankModels.value
  }

  function isPoolModelActive(pool, provider, model) {
    return poolModels(pool).some(m => m.model === model && m.provider === provider)
  }

  function selectProvider(name) {
    selectedName.value = name
  }

  async function saveProvider(data) {
    await api.saveProvider(data)
    selectedName.value = data.name
    await loadProviders()
  }

  async function removeProvider(name) {
    await api.deleteProvider(name)
    if (selectedName.value === name) selectedName.value = ''
    await loadProviders()
    await loadStatus()
  }

  async function fetchModels(data) {
    const result = await api.fetchProviderModels(data)
    await loadProviders()
    return result
  }

  async function activateModel(provider, model) {
    await api.activateModel(provider, model)
    await loadStatus()
    await loadProviders()
  }

  async function deactivateModel(provider, model) {
    await api.deactivateModel(provider, model)
    await loadStatus()
    await loadProviders()
  }

  async function switchModel(provider, model) {
    await api.selectModel(provider, model)
    await loadStatus()
  }

  async function activatePoolModel(pool, provider, model) {
    await api.activatePoolModel(pool, provider, model)
    await loadStatus()
    await loadProviders()
  }

  async function deactivatePoolModel(pool, provider, model) {
    await api.deactivatePoolModel(pool, provider, model)
    await loadStatus()
  }

  async function selectPoolModel(pool, provider, model) {
    await api.selectPoolModel(pool, provider, model)
    await loadStatus()
  }

  async function savePoolModelConfig(pool, provider, model, config) {
    await api.savePoolModelConfig(pool, provider, model, config)
    await loadStatus()
  }

  instance = {
    providers,
    selectedName,
    selectedProvider,
    providerList,
    activeModels,
    activeEmbeddingModels,
    activeRerankModels,
    activeModel,
    activeModelProvider,
    activeEmbeddingModel,
    activeEmbeddingProvider,
    activeRerankModel,
    activeRerankProvider,
    loading,
    loadProviders,
    loadStatus,
    isModelActive,
    isPoolModelActive,
    selectProvider,
    saveProvider,
    removeProvider,
    fetchModels,
    activateModel,
    deactivateModel,
    switchModel,
    activatePoolModel,
    deactivatePoolModel,
    selectPoolModel,
    savePoolModelConfig,
  }
  return instance
}
