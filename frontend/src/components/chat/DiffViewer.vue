<template>
  <div class="diff-viewer" v-if="lines.length">
    <div class="diff-summary">
      <span class="diff-file">{{ fileName }}</span>
      <span class="diff-stat add">+{{ addCount }}</span>
      <span class="diff-stat del">-{{ delCount }}</span>
    </div>
    <div class="diff-lines">
      <div
        v-for="(line, i) in lines"
        :key="i"
        :class="['diff-line', line.cls]"
      >
        <span class="diff-gutter">{{ line.mark }}</span>
        <code>{{ line.text || ' ' }}</code>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  diff: { type: String, default: '' },
  raw: { type: String, default: '' },
  fileName: { type: String, default: '' },
})

const parsed = computed(() => {
  if (!props.diff) return { lines: [], addCount: 0, delCount: 0, fileName: '' }

  const rawLines = props.diff.split(/\r?\n/).filter((l, i, arr) => i < arr.length - 1 || l !== '')
  let fileName = props.fileName
  const lines = rawLines.map(line => {
    let cls = 'ctx', mark = ' ', text = line
    if (line.startsWith('+++') || line.startsWith('---')) {
      cls = 'file'
      if (!fileName && line.startsWith('+++ b/')) fileName = line.slice(6)
    } else if (line.startsWith('@@')) {
      cls = 'meta'
    } else if (line.startsWith('+')) {
      cls = 'add'; mark = '+'; text = line.slice(1)
    } else if (line.startsWith('-')) {
      cls = 'del'; mark = '-'; text = line.slice(1)
    } else if (line.startsWith(' ')) {
      text = line.slice(1)
    }
    return { cls, mark, text }
  })

  return {
    lines,
    addCount: lines.filter(l => l.cls === 'add').length,
    delCount: lines.filter(l => l.cls === 'del').length,
    fileName: fileName || (props.raw || '').split(/\r?\n/, 1)[0] || 'File diff',
  }
})

const lines = computed(() => parsed.value.lines)
const addCount = computed(() => parsed.value.addCount)
const delCount = computed(() => parsed.value.delCount)
const fileName = computed(() => parsed.value.fileName)
</script>

<style scoped>
.diff-viewer {
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 7px;
  max-height: 360px;
  overflow: auto;
  font-size: 11px;
  line-height: 1.48;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
}

.diff-summary {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-bottom: 1px solid #30363d;
  color: #8b949e;
  background: #161b22;
  font-family: var(--mono);
  font-size: 11px;
}

.diff-file { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.diff-stat.add { color: #7ee787; }
.diff-stat.del { color: #ffa198; }

.diff-line {
  display: grid;
  grid-template-columns: 34px minmax(max-content, 1fr);
  min-width: 100%;
  font-family: var(--mono);
  white-space: pre;
}

.diff-line.add { background: rgba(46, 160, 67, 0.18); }
.diff-line.add .diff-gutter {
  color: #7ee787; background: rgba(46, 160, 67, 0.25);
  border-right-color: rgba(46, 160, 67, 0.35);
}
.diff-line.add code { color: #d2ffd9; }

.diff-line.del { background: rgba(248, 81, 73, 0.18); }
.diff-line.del .diff-gutter {
  color: #ffa198; background: rgba(248, 81, 73, 0.25);
  border-right-color: rgba(248, 81, 73, 0.35);
}
.diff-line.del code { color: #ffd7d5; }

.diff-line.meta { background: rgba(56, 139, 253, 0.12); }
.diff-line.meta .diff-gutter {
  color: #79c0ff; background: rgba(56, 139, 253, 0.18);
  border-right-color: rgba(56, 139, 253, 0.3);
}
.diff-line.meta code { color: #a5d6ff; }

.diff-line.file { background: #161b22; }
.diff-line.file code { color: #8b949e; }

.diff-line code { display: block; padding: 1px 12px; color: #c9d1d9; font-family: inherit; }

.diff-gutter {
  padding: 1px 9px 1px 0;
  text-align: right;
  color: #6e7681;
  background: rgba(110, 118, 129, 0.08);
  border-right: 1px solid rgba(48, 54, 61, 0.7);
  user-select: none;
}
</style>
