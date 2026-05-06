import { reactive } from 'vue'

const BASE = ''

const auth = reactive({
  loading: true,
  submitting: false,
  authRequired: false,
  setupRequired: false,
  authenticated: false,
  username: '',
  error: '',
})

let initPromise = null

async function authRequest(url, options = {}) {
  const { headers, ...rest } = options
  const res = await fetch(BASE + url, {
    ...rest,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(body.error || `HTTP ${res.status}`)
  }
  return body
}

function applyStatus(data) {
  auth.authRequired = !!data.auth_required
  auth.setupRequired = !!data.setup_required
  auth.authenticated = !!data.authenticated
  auth.username = data.username || ''
  auth.error = ''
}

export function markSetupRequired(message = 'setup required') {
  auth.loading = false
  auth.authRequired = false
  auth.setupRequired = true
  auth.authenticated = false
  auth.error = message
}

export function markUnauthenticated(message = 'authentication required') {
  auth.loading = false
  auth.setupRequired = false
  auth.authRequired = true
  auth.authenticated = false
  auth.error = message
}

export function useAuth() {
  async function refreshAuthStatus({ setLoading = true } = {}) {
    if (setLoading) auth.loading = true
    try {
      const data = await authRequest('/api/auth/status')
      applyStatus(data)
      return data
    } catch (e) {
      markUnauthenticated(e.message || 'unable to check authorization')
      throw e
    } finally {
      if (setLoading) auth.loading = false
    }
  }

  async function login(username, password) {
    auth.submitting = true
    auth.error = ''
    try {
      await authRequest('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      auth.authRequired = true
      auth.setupRequired = false
      auth.authenticated = true
      auth.error = ''
    } catch (e) {
      markUnauthenticated(e.message || 'invalid username or password')
      throw e
    } finally {
      auth.submitting = false
    }
  }

  async function setup(username, password) {
    auth.submitting = true
    auth.error = ''
    try {
      await authRequest('/api/auth/setup', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      auth.authRequired = true
      auth.setupRequired = false
      auth.authenticated = true
      auth.username = username
      auth.error = ''
    } catch (e) {
      markSetupRequired(e.message || 'setup failed')
      throw e
    } finally {
      auth.submitting = false
    }
  }

  async function initAuth() {
    if (initPromise) return initPromise
    initPromise = (async () => {
      auth.loading = true
      try {
        await refreshAuthStatus({ setLoading: false })
      } finally {
        auth.loading = false
      }
    })()
    return initPromise
  }

  return {
    auth,
    initAuth,
    login,
    setup,
    refreshAuthStatus,
  }
}
