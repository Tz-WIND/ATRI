import { ref, computed } from 'vue'
import { useApi } from './useApi.js'

let instance = null

export function useProviders() {
  if (instance) return instance

  const api = useApi()
  const providers = ref({})
  const selectedName = ref('')
  const activeModels = ref([])
  const activeModel = ref('')
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
      activeModels.value = s.active_models || []
    } catch {}
  }

  function isModelActive(provider, model) {
    return activeModels.value.some(m => m.model === model && m.provider === provider)
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

  instance = {
    providers,
    selectedName,
    selectedProvider,
    providerList,
    activeModels,
    activeModel,
    loading,
    loadProviders,
    loadStatus,
    isModelActive,
    selectProvider,
    saveProvider,
    removeProvider,
    fetchModels,
    activateModel,
    deactivateModel,
    switchModel,
  }
  return instance
}
