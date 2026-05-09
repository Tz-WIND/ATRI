<template>
  <div class="page">
    <PageHeader title="Workspace" />
    <div class="page-body">
      <section class="form-section">
        <h3>Agent Workspace Directory</h3>
        <p class="desc">
          All file operations (read, write, edit, grep, glob) are sandboxed to this directory.
        </p>
        <div class="field">
          <label>Workspace Path</label>
          <input
            v-model="path"
            placeholder="./workspace"
          >
        </div>
        <button
          class="btn btn-primary"
          @click="save"
        >
          Save
        </button>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import { useApi } from '@/composables/useApi.js'

const api = useApi()
const path = ref('')

onMounted(async () => {
  try {
    const d = await api.getWorkspace()
    path.value = d.workspace || ''
  } catch {}
})

async function save() {
  await api.saveWorkspace(path.value)
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.page-body { flex: 1; overflow-y: auto; padding: 22px 24px; }

.form-section {
  max-width: 760px;
  margin-bottom: 24px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.032);
  padding: 16px;
}
.form-section h3 {
  font-size: 14px; letter-spacing: 0;
  color: var(--t1); margin-bottom: 6px; font-weight: 650;
}

.desc { font-size: 13px; color: var(--t2); margin-bottom: 12px; }

.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--t2); margin-bottom: 4px; font-family: var(--mono); }
.field input {
  width: 100%; background: rgba(24, 24, 24, 0.66); border: 1px solid var(--border-input);
  border-radius: 7px; color: var(--t1); padding: 8px 12px; font-size: 13px;
  font-family: var(--mono); outline: none;
}
.field input:focus { border-color: rgba(158, 191, 255, 0.5); box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12); }

.btn {
  padding: 6px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; font-size: 12px; font-weight: 600; font-family: var(--mono);
  transition: all 0.12s;
}
.btn-primary { background: var(--acc-bg); color: var(--acc2); border-color: rgba(125,168,232,0.3); }
.btn-primary:hover { background: var(--acc-bg-strong); }
</style>
