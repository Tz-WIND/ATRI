<template>
  <div class="editor-area">
    <div
      v-if="tabs.length === 0"
      class="editor-empty"
    >
      <div class="empty-icon">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
        >
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
          <polyline points="14 2 14 8 20 8" />
        </svg>
      </div>
      <div class="empty-text">
        No files open
      </div>
      <div class="empty-sub">
        Double-click a file in the file manager to open it here
      </div>
    </div>
    <template v-else>
      <div class="tab-bar">
        <div
          ref="tabListRef"
          class="tab-list"
        >
          <div
            v-for="tab in tabs"
            :key="tab.path"
            :class="['tab', { active: tab.path === activeTabPath, modified: tab.modified }]"
            @click="activateTab(tab.path)"
            @auxclick.prevent="closeTab(tab.path)"
          >
            <span class="tab-name">{{ tab.name }}</span>
            <button
              class="tab-close"
              title="Close"
              @click.stop="closeTab(tab.path)"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <line
                  x1="18"
                  y1="6"
                  x2="6"
                  y2="18"
                /><line
                  x1="6"
                  y1="6"
                  x2="18"
                  y2="18"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>
      <div
        v-if="activeTab"
        class="editor-content"
      >
        <div class="code-wrapper">
          <div class="line-numbers">
            <div
              ref="lineNumbersRef"
              class="line-numbers-inner"
            >
              <div
                v-for="n in lineCount"
                :key="n"
                :class="['line-num', { active: n === currentLine }]"
              >
                {{ n }}
              </div>
            </div>
          </div>
          <div
            class="current-line-highlight"
            :style="currentLineStyle"
          />
          <textarea
            ref="editorRef"
            class="code-editor"
            :value="activeTab.content"
            spellcheck="false"
            wrap="off"
            @input="onInput"
            @scroll="syncScroll"
            @keydown="onKeydown"
            @click="updateCursorState"
            @mouseup="updateCursorState"
            @select="updateCursorState"
            @keyup="updateCursorState"
            @focus="updateCursorState"
          />
          <pre
            ref="highlightRef"
            class="code-highlight"
          ><code v-html="highlightedCode" /></pre>
          <pre
            ref="wordHighlightsRef"
            class="word-highlights"
          ><code v-html="wordHighlightsHtml" /></pre>
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
const wordHighlightsRef = ref(null)
const lineNumbersRef = ref(null)
const tabListRef = ref(null)
const currentLine = ref(1)
const selectedWord = ref('')
const editorScrollTop = ref(0)
const EDITOR_PADDING_TOP = 12
const EDITOR_LINE_HEIGHT = 28

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

const currentLineStyle = computed(() => {
  const top = EDITOR_PADDING_TOP + (currentLine.value - 1) * EDITOR_LINE_HEIGHT
  return {
    top: `${top - editorScrollTop.value}px`,
  }
})

const wordHighlightsHtml = computed(() => {
  if (!activeTab.value || !selectedWord.value) return ''
  const code = activeTab.value.content || ''
  const word = selectedWord.value
  const escaped = escapeRegExp(word)
  const regex = new RegExp(escaped, 'g')
  let result = ''
  let lastIdx = 0
  let match
  while ((match = regex.exec(code)) !== null) {
    const before = code.slice(lastIdx, match.index)
    result += escapeHtml(before)
    result += `<mark class="word-match">${escapeHtml(match[0])}</mark>`
    lastIdx = match.index + match[0].length
  }
  result += escapeHtml(code.slice(lastIdx))
  return result
})

function escapeRegExp(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function updateCursorState() {
  if (!editorRef.value) return
  const ta = editorRef.value
  const pos = ta.selectionStart
  const text = ta.value || ''
  const line = text.substring(0, pos).split('\n').length
  currentLine.value = line

  const start = ta.selectionStart
  const end = ta.selectionEnd
  if (start !== end) {
    const selected = text.substring(start, end)
    if (selected.length >= 2 && selected.length <= 60 && /^\w+$/.test(selected)) {
      if (selectedWord.value !== selected) {
        selectedWord.value = selected
        nextTick(syncWordHighlightsScroll)
      }
    } else {
      selectedWord.value = ''
    }
  } else {
    selectedWord.value = ''
  }
}

function syncWordHighlightsScroll() {
  if (wordHighlightsRef.value && editorRef.value) {
    const st = editorRef.value.scrollTop
    const sl = editorRef.value.scrollLeft
    wordHighlightsRef.value.style.transform = `translate(${-sl}px, ${-st}px)`
  }
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
  nextTick(updateCursorState)
}

function syncScroll() {
  if (!editorRef.value) return
  const st = editorRef.value.scrollTop
  const sl = editorRef.value.scrollLeft
  editorScrollTop.value = st
  if (highlightRef.value) {
    highlightRef.value.style.transform = `translate(${-sl}px, ${-st}px)`
  }
  if (wordHighlightsRef.value) {
    wordHighlightsRef.value.style.transform = `translate(${-sl}px, ${-st}px)`
  }
  if (lineNumbersRef.value) {
    lineNumbersRef.value.style.transform = `translateY(${-st}px)`
  }
  updateCursorState()
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
  content: '';
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-left: 4px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.12);
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
  background: var(--bg0);
  --editor-gutter-w: 48px;
  --editor-line-height: 28px;
  --editor-pad-y: 12px;
  --editor-pad-x: 16px;
}

.line-numbers {
  width: var(--editor-gutter-w);
  flex-shrink: 0;
  overflow: hidden;
  background: transparent;
  border-right: 0;
  user-select: none;
  position: relative;
}

.line-numbers-inner {
  padding: var(--editor-pad-y) 0;
}

.line-num {
  height: var(--editor-line-height);
  line-height: var(--editor-line-height);
  text-align: right;
  padding-right: 10px;
  font-size: 12px;
  font-family: var(--mono);
  color: rgba(133, 133, 133, 0.72);
}

.current-line-highlight {
  position: absolute;
  left: var(--editor-gutter-w);
  right: 0;
  height: var(--editor-line-height);
  background: rgba(255, 255, 255, 0.055);
  pointer-events: none;
  z-index: 2;
}

.line-num.active {
  color: var(--t2);
  background: rgba(255, 255, 255, 0.055);
}

.code-editor {
  position: absolute;
  top: 0;
  left: var(--editor-gutter-w);
  right: 0;
  bottom: 0;
  padding: var(--editor-pad-y) var(--editor-pad-x);
  font-size: 13px;
  font-family: var(--mono);
  line-height: var(--editor-line-height);
  tab-size: 2;
  white-space: pre;
  margin: 0;
  box-sizing: border-box;
  background: transparent;
  color: transparent;
  caret-color: var(--t1);
  border: none;
  outline: none;
  resize: none;
  z-index: 5;
  overflow: scroll;
  scrollbar-width: none;
}

.code-editor::-webkit-scrollbar {
  display: none;
}

.code-highlight,
.word-highlights {
  position: absolute;
  top: 0;
  left: var(--editor-gutter-w);
  padding: var(--editor-pad-y) var(--editor-pad-x);
  font-size: 13px;
  font-family: var(--mono);
  line-height: var(--editor-line-height);
  tab-size: 2;
  white-space: pre;
  margin: 0;
  box-sizing: border-box;
  pointer-events: none;
  width: max-content;
  min-width: calc(100% - var(--editor-gutter-w));
}

.code-highlight {
  z-index: 1;
  background: transparent;
  color: var(--t1);
}

.code-highlight code {
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
}

.word-highlights {
  z-index: 3;
  background: transparent;
  color: transparent;
}

.word-highlights code {
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
  color: transparent;
}
</style>

<style>
.word-highlights .word-match {
  background: rgba(255, 200, 50, 0.18);
  outline: 0;
  border-radius: 2px;
  color: transparent;
  padding: 0;
  margin: 0;
  border: none;
}

/* highlight.js workbench theme */
.code-highlight .hljs-keyword { color: var(--code-keyword); }
.code-highlight .hljs-built_in { color: var(--code-built-in); }
.code-highlight .hljs-type { color: var(--code-type); }
.code-highlight .hljs-literal { color: var(--code-literal); }
.code-highlight .hljs-number { color: var(--code-number); }
.code-highlight .hljs-string { color: var(--code-string); }
.code-highlight .hljs-regexp { color: var(--code-regexp); }
.code-highlight .hljs-symbol { color: var(--code-literal); }
.code-highlight .hljs-variable { color: var(--code-variable); }
.code-highlight .hljs-template-variable { color: var(--code-variable); }
.code-highlight .hljs-link { color: var(--code-string); }
.code-highlight .hljs-selector-class { color: var(--code-selector); }
.code-highlight .hljs-selector-id { color: var(--code-selector); }
.code-highlight .hljs-comment { color: var(--code-comment); font-style: italic; }
.code-highlight .hljs-doctag { color: var(--code-comment); }
.code-highlight .hljs-meta { color: var(--code-meta); }
.code-highlight .hljs-meta .hljs-keyword { color: var(--code-literal); }
.code-highlight .hljs-meta .hljs-string { color: var(--code-string); }
.code-highlight .hljs-section { color: var(--code-literal); }
.code-highlight .hljs-tag { color: var(--code-literal); }
.code-highlight .hljs-name { color: var(--code-literal); }
.code-highlight .hljs-attr { color: var(--code-variable); }
.code-highlight .hljs-attribute { color: var(--code-variable); }
.code-highlight .hljs-title { color: var(--code-built-in); }
.code-highlight .hljs-title.function_ { color: var(--code-built-in); }
.code-highlight .hljs-title.class_ { color: var(--code-type); }
.code-highlight .hljs-params { color: var(--code-variable); }
.code-highlight .hljs-property { color: var(--code-variable); }
.code-highlight .hljs-subst { color: var(--code-text); }
.code-highlight .hljs-formula { color: var(--code-keyword); }
.code-highlight .hljs-addition { color: var(--acc2); background: rgba(55,148,255,0.1); }
.code-highlight .hljs-deletion { color: var(--code-string); background: rgba(244,135,113,0.1); }
.code-highlight .hljs-punctuation { color: var(--code-text); }
</style>
