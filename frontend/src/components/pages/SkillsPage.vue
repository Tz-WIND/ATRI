<template>
  <div class="page">
    <PageHeader title="Skills" />
    <div class="page-body">
      <p class="desc">
        Skills are discovered from the configured <code>skills/</code> install directory, workspace skill folders, and compatible tool directories.
      </p>

      <div class="upload-bar">
        <input
          ref="fileInput"
          type="file"
          accept=".zip"
          class="file-input"
          @change="onFileSelected"
        >
        <button
          class="btn btn-primary"
          @click="triggerUpload"
        >
          Upload .zip Skill
        </button>
        <span
          v-if="uploadMsg"
          :class="['upload-msg', uploadOk ? 'ok' : 'err']"
        >
          {{ uploadMsg }}
        </span>
      </div>

      <div
        v-if="skills.length === 0"
        class="empty"
      >
        No skills found. Add SKILL.md files to the skills/ directory or upload a .zip.
      </div>

      <div
        v-for="s in skills"
        :key="`${s.name}:${s.path}`"
        class="card"
      >
        <div class="card-header">
          <span
            class="card-title"
            @click="toggleDetail(s.name)"
          >{{ s.name }}</span>
          <StatusBadge :type="s.active ? 'on' : 'off'">
            {{ s.active ? 'active' : 'inactive' }}
          </StatusBadge>
        </div>
        <div class="card-meta">
          {{ s.description }}
        </div>
        <div class="card-tags">
          <span class="tag">{{ s.source || 'configured' }}</span>
          <span
            v-if="s.format"
            class="tag"
          >{{ s.format }}</span>
          <span
            v-if="s.companion_files?.length"
            class="tag"
          >{{ s.companion_files.length }} files</span>
        </div>
        <div
          class="card-meta"
          style="font-size:10px;opacity:0.6"
        >
          {{ s.path }}
        </div>
        <div class="card-actions">
          <button
            class="btn btn-ghost"
            @click="toggleSkill(s.name, !s.active)"
          >
            {{ s.active ? 'Disable' : 'Enable' }}
          </button>
          <button
            class="btn btn-ghost btn-danger"
            :disabled="!s.can_delete"
            :title="s.can_delete ? 'Delete skill' : 'Only skills in the configured install directory can be deleted'"
            @click="removeSkill(s.name)"
          >
            Delete
          </button>
        </div>
        <div
          v-if="s.warnings?.length"
          class="warnings"
        >
          {{ s.warnings.join(' ') }}
        </div>
        <div
          v-if="expandedName === s.name"
          class="card-detail"
        >
          <div
            v-if="detailLoading"
            class="loading"
          >
            Loading...
          </div>
          <pre
            v-else
            class="skill-content"
          >{{ detailContent }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useApi } from '@/composables/useApi.js'

const api = useApi()
const skills = ref([])
const fileInput = ref(null)
const uploadMsg = ref('')
const uploadOk = ref(false)
const expandedName = ref(null)
const detailContent = ref('')
const detailLoading = ref(false)

onMounted(loadSkills)

async function loadSkills() {
  try {
    skills.value = await api.getSkills()
  } catch { skills.value = [] }
}

async function toggleSkill(name, active) {
  await api.updateSkill(name, { active })
  await loadSkills()
}

async function removeSkill(name) {
  const skill = skills.value.find((s) => s.name === name)
  if (skill && !skill.can_delete) return
  if (!confirm(`Delete skill "${name}"? This will remove the directory from disk.`)) return
  try {
    await api.deleteSkill(name)
    if (expandedName.value === name) expandedName.value = null
    await loadSkills()
  } catch (e) {
    uploadMsg.value = e.message
    uploadOk.value = false
  }
}

async function toggleDetail(name) {
  if (expandedName.value === name) {
    expandedName.value = null
    return
  }
  expandedName.value = name
  detailLoading.value = true
  detailContent.value = ''
  try {
    const data = await api.getSkill(name)
    detailContent.value = data.content || '(empty)'
  } catch {
    detailContent.value = '(failed to load)'
  } finally {
    detailLoading.value = false
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

async function onFileSelected(e) {
  const file = e.target.files?.[0]
  if (!file) return
  uploadMsg.value = ''
  uploadOk.value = false
  try {
    const res = await api.uploadSkill(file)
    uploadMsg.value = `Installed: ${res.installed}`
    uploadOk.value = true
    await loadSkills()
  } catch (e) {
    uploadMsg.value = e.message
    uploadOk.value = false
  }
  // reset input so same file can be re-uploaded
  e.target.value = ''
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.page-body { flex: 1; overflow-y: auto; padding: 20px; }

.desc { font-size: 13px; color: var(--t2); margin-bottom: 12px; }
.desc code { color: var(--acc2); }

.upload-bar {
  display: flex; align-items: center; gap: 10px; margin-bottom: 16px;
}
.file-input { display: none; }
.upload-msg { font-size: 12px; }
.upload-msg.ok { color: var(--acc1); }
.upload-msg.err { color: var(--red, #e0556a); }

.card {
  background: var(--bg1); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px; margin-bottom: 10px;
}

.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.card-title {
  font-size: 13px; font-weight: 600; font-family: var(--mono); color: var(--t1);
  cursor: pointer;
}
.card-title:hover { color: var(--acc2); }
.card-meta { font-size: 11px; color: var(--t3); margin-top: 2px; }
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.tag {
  font-size: 10px; color: var(--t2); background: var(--bg2);
  border: 1px solid var(--border); border-radius: 6px; padding: 3px 7px;
  font-family: var(--mono);
}
.card-actions { margin-top: 8px; display: flex; gap: 6px; }
.warnings {
  margin-top: 8px; font-size: 11px; color: var(--yellow, #c8903f);
  background: color-mix(in srgb, var(--yellow, #c8903f) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--yellow, #c8903f) 25%, transparent);
  border-radius: 6px; padding: 7px 9px;
}

.card-detail {
  margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px;
}
.skill-content {
  font-size: 11px; font-family: var(--mono); color: var(--t2);
  white-space: pre-wrap; word-break: break-all;
  max-height: 400px; overflow-y: auto;
  background: var(--bg2); padding: 10px; border-radius: 6px;
}
.loading { font-size: 12px; color: var(--t3); }

.empty { color: var(--t3); font-size: 13px; }

.btn {
  padding: 6px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; font-size: 12px; font-weight: 600; font-family: var(--mono);
  transition: all 0.12s;
}
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:disabled:hover { background: none; color: var(--t2); border-color: var(--border); }
.btn-primary { background: var(--acc2); color: #fff; border-color: var(--acc2); }
.btn-primary:hover { opacity: 0.85; }
.btn-ghost { background: none; color: var(--t2); }
.btn-ghost:hover { background: var(--bg2); color: var(--t1); }
.btn-danger:hover { color: var(--red, #e0556a); border-color: var(--red, #e0556a); }
</style>
