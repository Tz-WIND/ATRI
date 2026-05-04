<template>
  <div class="editor-area">
    <div v-if="tabs.length === 0" class="editor-empty">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
      </div>
      <div class="empty-text">No files open</div>
      <div class="empty-sub">Double-click a file in the file manager to open it here</div>
    </div>
    <template v-else>
      <div class="tab-bar">
        <div class="tab-list" ref="tabListRef">
          <div
            v-for="tab in tabs"
            :key="tab.path"
            :class="['tab', { active: tab.path === activeTabPath, modified: tab.modified }]"
            @click="activateTab(tab.path)"
            @auxclick.prevent="closeTab(tab.path)"
          >
            <span class="tab-name">{{ tab.name }}</span>
            <button class="tab-close" @click.stop="closeTab(tab.path)" title="Close">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
      <div class="editor-content" v-if="activeTab">
        <div class="editor-toolbar">
          <span class="editor-path">{{ activeTab.path }}</span>
          <div class="editor-actions">
            <span v-if="activeTab.modified" class="modified-badge">Modified</span>
            <button class="tool-btn" @click="saveFile" :disabled="!activeTab.modified || saving" title="Save (Ctrl+S)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/>
                <polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>
              </svg>
              Save
            </button>
          </div>
        </div>
        <div class="code-wrapper">
          <div class="line-numbers" ref="lineNumbersRef">
            <div v-for="n in lineCount" :key="n" class="line-num">{{ n }}</div>
          </div>
          <textarea
            ref="editorRef"
            class="code-editor"
            :value="activeTab.content"
            @input="onInput"
            @scroll="syncScroll"
            @keydown="onKeydown"
            spellcheck="false"
            wrap="off"
          ></textarea>
          <pre class="code-highlight" ref="highlightRef"><code v-html="highlightedCode"></code></pre>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import hljs from 'highlight.js'
import { useApi } from '@/composables/useApi.js'

const api = useApi()
const tabs = ref([])
const activeTabPath = ref('')
const saving = ref(false)
const editorRef = ref(null)
const highlightRef = ref(null)
const lineNumbersRef = ref(null)
const tabListRef = ref(null)

const activeTab = computed(() => tabs.value.find(t => t.path === activeTabPath.value))

const lineCount = computed(() => {
  if (!activeTab.value) return 0
  return (activeTab.value.content || '').split('\n').length
})

const extLangMap = {
  py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
  vue: 'xml', html: 'xml', xml: 'xml', svg: 'xml',
  css: 'css', scss: 'scss', less: 'less',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'ini',
  md: 'markdown', sh: 'bash', bash: 'bash', zsh: 'bash',
  rs: 'rust', go: 'go', java: 'java', c: 'c', cpp: 'cpp', h: 'c',
  rb: 'ruby', php: 'php', sql: 'sql', lua: 'lua',
  swift: 'swift', kt: 'kotlin', r: 'r', zig: 'zig',
}

function detectLang(name) {
  const ext = name.split('.').pop().toLowerCase()
  return extLangMap[ext] || null
}

const highlightedCode = computed(() => {
  if (!activeTab.value) return ''
  const code = activeTab.value.content || ''
  const lang = detectLang(activeTab.value.name)
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  } catch {
    return escapeHtml(code)
  }
})

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

async function openFile(fileInfo) {
  const existing = tabs.value.find(t => t.path === fileInfo.path)
  if (existing) {
    activeTabPath.value = existing.path
    return
  }
  try {
    const data = await api.readFile(fileInfo.path)
    tabs.value.push({
      path: fileInfo.path,
      name: fileInfo.name,
      content: data.content,
      originalContent: data.content,
      modified: false,
    })
    activeTabPath.value = fileInfo.path
  } catch (err) {
    console.error('Failed to open file:', err)
  }
}

function activateTab(path) {
  activeTabPath.value = path
}

function closeTab(path) {
  const tab = tabs.value.find(t => t.path === path)
  if (tab && tab.modified) {
    if (!confirm(`"${tab.name}" has unsaved changes. Close anyway?`)) return
  }
  const idx = tabs.value.findIndex(t => t.path === path)
  tabs.value.splice(idx, 1)
  if (activeTabPath.value === path) {
    if (tabs.value.length > 0) {
      const next = Math.min(idx, tabs.value.length - 1)
      activeTabPath.value = tabs.value[next].path
    } else {
      activeTabPath.value = ''
    }
  }
}

function onInput(e) {
  if (!activeTab.value) return
  activeTab.value.content = e.target.value
  activeTab.value.modified = activeTab.value.content !== activeTab.value.originalContent
}

function syncScroll() {
  if (!editorRef.value || !highlightRef.value || !lineNumbersRef.value) return
  highlightRef.value.scrollTop = editorRef.value.scrollTop
  highlightRef.value.scrollLeft = editorRef.value.scrollLeft
  lineNumbersRef.value.scrollTop = editorRef.value.scrollTop
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    saveFile()
    return
  }
  if (e.key === 'Tab') {
    e.preventDefault()
    const ta = editorRef.value
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const val = ta.value
    ta.value = val.substring(0, start) + '  ' + val.substring(end)
    ta.selectionStart = ta.selectionEnd = start + 2
    if (activeTab.value) {
      activeTab.value.content = ta.value
      activeTab.value.modified = activeTab.value.content !== activeTab.value.originalContent
    }
  }
}

async function saveFile() {
  if (!activeTab.value || !activeTab.value.modified) return
  saving.value = true
  try {
    await api.writeFile(activeTab.value.path, activeTab.value.content)
    activeTab.value.originalContent = activeTab.value.content
    activeTab.value.modified = false
  } catch (err) {
    console.error('Failed to save file:', err)
    alert('Save failed: ' + err.message)
  } finally {
    saving.value = false
  }
}

watch(activeTabPath, () => {
  nextTick(() => {
    if (editorRef.value) {
      editorRef.value.focus()
    }
  })
})

defineExpose({ openFile })
</script>

<style scoped>
.editor-area {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg0);
}

.editor-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--t3);
  gap: 8px;
}

.empty-icon {
  width: 48px;
  height: 48px;
  color: var(--bg3);
}

.empty-icon svg {
  width: 48px;
  height: 48px;
}

.empty-text {
  font-size: 14px;
  font-weight: 600;
}

.empty-sub {
  font-size: 12px;
  opacity: 0.7;
}

.tab-bar {
  display: flex;
  background: var(--bg1);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  overflow: hidden;
}

.tab-list {
  display: flex;
  overflow-x: auto;
  flex: 1;
}

.tab-list::-webkit-scrollbar {
  height: 0;
}

.tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: 12px;
  font-family: var(--mono);
  color: var(--t3);
  border-right: 1px solid var(--border);
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
  position: relative;
}

.tab:hover {
  background: var(--bg2);
  color: var(--t2);
}

.tab.active {
  background: var(--bg0);
  color: var(--t1);
}

.tab.active::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--bg0);
}

.tab.modified .tab-name::after {
  content: '●';
  margin-left: 4px;
  color: var(--orange);
  font-size: 10px;
}

.tab-close {
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: var(--t3);
  cursor: pointer;
  border-radius: 3px;
  opacity: 0;
  transition: all 0.1s;
}

.tab:hover .tab-close,
.tab.active .tab-close {
  opacity: 1;
}

.tab-close:hover {
  background: var(--bg3);
  color: var(--t1);
}

.tab-close svg {
  width: 12px;
  height: 12px;
}

.editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg1);
  flex-shrink: 0;
}

.editor-path {
  font-size: 11px;
  color: var(--t3);
  font-family: var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.modified-badge {
  font-size: 10px;
  color: var(--orange);
  font-family: var(--mono);
}

.tool-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  font-size: 11px;
  font-family: var(--mono);
  background: rgba(63,185,80,0.12);
  color: var(--green);
  border: 1px solid rgba(63,185,80,0.25);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s;
}

.tool-btn:hover:not(:disabled) {
  background: rgba(63,185,80,0.22);
}

.tool-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.tool-btn svg {
  width: 13px;
  height: 13px;
}

.editor-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.code-wrapper {
  flex: 1;
  position: relative;
  overflow: hidden;
  display: flex;
}

.line-numbers {
  width: 48px;
  flex-shrink: 0;
  overflow: hidden;
  background: var(--bg1);
  border-right: 1px solid var(--border);
  padding: 8px 0;
  user-select: none;
}

.line-num {
  height: 20px;
  line-height: 20px;
  text-align: right;
  padding-right: 12px;
  font-size: 12px;
  font-family: var(--mono);
  color: var(--t3);
}

.code-editor,
.code-highlight {
  position: absolute;
  top: 0;
  left: 48px;
  right: 0;
  bottom: 0;
  padding: 8px 12px;
  font-size: 13px;
  font-family: var(--mono);
  line-height: 20px;
  tab-size: 2;
  white-space: pre;
  overflow: auto;
  margin: 0;
}

.code-editor {
  background: transparent;
  color: transparent;
  caret-color: var(--t1);
  border: none;
  outline: none;
  resize: none;
  z-index: 2;
}

.code-highlight {
  pointer-events: none;
  z-index: 1;
  background: var(--bg0);
  color: var(--t1);
}

.code-highlight code {
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
}
</style>

<style>
/* highlight.js VS Code dark theme */
.code-highlight .hljs-keyword { color: #c586c0; }
.code-highlight .hljs-built_in { color: #dcdcaa; }
.code-highlight .hljs-type { color: #4ec9b0; }
.code-highlight .hljs-literal { color: #569cd6; }
.code-highlight .hljs-number { color: #b5cea8; }
.code-highlight .hljs-string { color: #ce9178; }
.code-highlight .hljs-regexp { color: #d16969; }
.code-highlight .hljs-symbol { color: #569cd6; }
.code-highlight .hljs-variable { color: #9cdcfe; }
.code-highlight .hljs-template-variable { color: #9cdcfe; }
.code-highlight .hljs-link { color: #ce9178; }
.code-highlight .hljs-selector-class { color: #d7ba7d; }
.code-highlight .hljs-selector-id { color: #d7ba7d; }
.code-highlight .hljs-comment { color: #6a9955; font-style: italic; }
.code-highlight .hljs-doctag { color: #608b4e; }
.code-highlight .hljs-meta { color: #9b9b9b; }
.code-highlight .hljs-meta .hljs-keyword { color: #569cd6; }
.code-highlight .hljs-meta .hljs-string { color: #ce9178; }
.code-highlight .hljs-section { color: #569cd6; }
.code-highlight .hljs-tag { color: #569cd6; }
.code-highlight .hljs-name { color: #569cd6; }
.code-highlight .hljs-attr { color: #9cdcfe; }
.code-highlight .hljs-attribute { color: #9cdcfe; }
.code-highlight .hljs-title { color: #dcdcaa; }
.code-highlight .hljs-title.function_ { color: #dcdcaa; }
.code-highlight .hljs-title.class_ { color: #4ec9b0; }
.code-highlight .hljs-params { color: #9cdcfe; }
.code-highlight .hljs-property { color: #9cdcfe; }
.code-highlight .hljs-subst { color: #d4d4d4; }
.code-highlight .hljs-formula { color: #c586c0; }
.code-highlight .hljs-addition { color: #b5cea8; background: rgba(63,185,80,0.1); }
.code-highlight .hljs-deletion { color: #ce9178; background: rgba(244,135,113,0.1); }
.code-highlight .hljs-punctuation { color: #d4d4d4; }
</style>
