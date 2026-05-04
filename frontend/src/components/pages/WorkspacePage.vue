<template>
  <div class="page">
    <PageHeader title="Workspace" />
    <div class="page-body">
      <section class="form-section">
        <h3>Agent Workspace Directory</h3>
        <p class="desc">All file operations (read, write, edit, grep, glob) are sandboxed to this directory.</p>
        <div class="field">
          <label>Workspace Path</label>
          <input v-model="path" placeholder="./workspace" />
        </div>
        <button class="btn btn-primary" @click="save">Save</button>
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
.page-body { flex: 1; overflow-y: auto; padding: 20px; }

.form-section { margin-bottom: 24px; }
.form-section h3 {
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--t3); margin-bottom: 12px; font-family: var(--mono);
}

.desc { font-size: 13px; color: var(--t2); margin-bottom: 12px; }

.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--t2); margin-bottom: 4px; font-family: var(--mono); }
.field input {
  width: 100%; background: var(--bg0); border: 1px solid var(--border);
  border-radius: 6px; color: var(--t1); padding: 8px 12px; font-size: 13px;
  font-family: var(--mono); outline: none;
}
.field input:focus { border-color: var(--acc); }

.btn {
  padding: 6px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; font-size: 12px; font-weight: 600; font-family: var(--mono);
  transition: all 0.12s;
}
.btn-primary { background: rgba(63,185,80,0.15); color: var(--green); border-color: rgba(63,185,80,0.3); }
.btn-primary:hover { background: rgba(63,185,80,0.25); }
</style>
