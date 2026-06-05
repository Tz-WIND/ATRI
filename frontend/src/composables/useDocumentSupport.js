import { ref } from 'vue'
import { useApi } from './useApi.js'

export function useDocumentSupport() {
  const api = useApi()
  const documentAccept = ref('')
  const documentExtensions = ref([])

  async function loadDocumentSupport() {
    try {
      const data = await api.getDocumentSupport()
      documentAccept.value = typeof data.accept === 'string' ? data.accept : ''
      documentExtensions.value = Array.isArray(data.extensions) ? data.extensions : []
    } catch {
      documentAccept.value = ''
      documentExtensions.value = []
    }
  }

  return {
    documentAccept,
    documentExtensions,
    loadDocumentSupport,
  }
}
