<template>
  <main class="auth-screen">
    <form
      class="auth-panel"
      @submit.prevent="submit"
    >
      <div
        class="auth-mark"
        aria-hidden="true"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path d="M12 2L2 7l10 5 10-5-10-5z" />
          <path d="M2 17l10 5 10-5" />
          <path d="M2 12l10 5 10-5" />
        </svg>
      </div>
      <h1>{{ title }}</h1>
      <label for="dashboard-username">Username</label>
      <input
        id="dashboard-username"
        ref="usernameInput"
        v-model.trim="username"
        type="text"
        autocomplete="username"
        spellcheck="false"
        placeholder="admin"
      >
      <label for="dashboard-password">Password</label>
      <input
        id="dashboard-password"
        ref="passwordInput"
        v-model="password"
        type="password"
        :autocomplete="auth.setupRequired ? 'new-password' : 'current-password'"
        spellcheck="false"
        placeholder="password"
      >
      <template v-if="auth.setupRequired">
        <label for="dashboard-confirm-password">Confirm Password</label>
        <input
          id="dashboard-confirm-password"
          v-model="confirmPassword"
          type="password"
          autocomplete="new-password"
          spellcheck="false"
          placeholder="password"
        >
      </template>
      <p
        v-if="errorText"
        class="auth-error"
      >
        {{ errorText }}
      </p>
      <button
        type="submit"
        :disabled="submitDisabled"
      >
        {{ buttonText }}
      </button>
    </form>
  </main>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useAuth } from '../../composables/useAuth.js'

const { auth, login, setup } = useAuth()
const username = ref(auth.username || 'admin')
const password = ref('')
const confirmPassword = ref('')
const localError = ref('')
const usernameInput = ref(null)
const passwordInput = ref(null)

const errorText = computed(() => localError.value || auth.error)
const title = computed(() => auth.setupRequired ? 'Create Dashboard Account' : 'ATRI Dashboard')
const buttonText = computed(() => {
  if (auth.submitting) return auth.setupRequired ? 'Creating...' : 'Authorizing...'
  return auth.setupRequired ? 'Create Account' : 'Authorize'
})
const submitDisabled = computed(() => {
  if (auth.submitting || !username.value || !password.value) return true
  return auth.setupRequired && !confirmPassword.value
})

async function submit() {
  localError.value = ''
  if (!username.value || !password.value) {
    localError.value = 'Username and password required'
    return
  }
  if (auth.setupRequired && password.value !== confirmPassword.value) {
    localError.value = 'Passwords do not match'
    confirmPassword.value = ''
    return
  }
  try {
    if (auth.setupRequired) {
      await setup(username.value, password.value)
    } else {
      await login(username.value, password.value)
    }
    password.value = ''
    confirmPassword.value = ''
  } catch {
    password.value = ''
    confirmPassword.value = ''
    await nextTick()
    passwordInput.value?.focus()
  }
}

onMounted(() => {
  passwordInput.value?.focus()
})
</script>

<style scoped>
.auth-screen {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-bg);
  color: var(--t1);
  padding: 24px;
}

.auth-panel {
  width: min(100%, 360px);
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 24px;
  background: var(--glass-strong);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-panel);
  backdrop-filter: blur(18px);
}

.auth-mark {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--acc2);
  background: var(--acc-bg);
  border: 1px solid rgba(125, 168, 232, 0.24);
  border-radius: 8px;
}

.auth-mark svg {
  width: 24px;
  height: 24px;
}

h1 {
  font-family: var(--mono);
  font-size: 16px;
  font-weight: 600;
  color: var(--t1);
}

label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--t2);
}

input {
  height: 34px;
  border: 1px solid var(--border-input);
  border-radius: 7px;
  background: rgba(24, 24, 24, 0.66);
  color: var(--t1);
  padding: 0 10px;
  font-family: var(--mono);
  font-size: 12px;
}

input:focus {
  outline: none;
  border-color: rgba(158, 191, 255, 0.5);
  box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12);
}

.auth-error {
  min-height: 18px;
  color: var(--red);
  font-size: 12px;
}

button {
  height: 34px;
  border: 1px solid rgba(125, 168, 232, 0.3);
  border-radius: 7px;
  background: var(--acc-bg);
  color: var(--acc2);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

button:hover:not(:disabled) {
  background: var(--acc-bg-strong);
  border-color: rgba(125, 168, 232, 0.42);
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
