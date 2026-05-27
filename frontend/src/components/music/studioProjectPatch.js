function cloneValue(value) {
  if (value == null || typeof value !== 'object') return value
  if (typeof structuredClone === 'function') return structuredClone(value)
  return JSON.parse(JSON.stringify(value))
}

function pointerTokens(path) {
  if (path === '') return []
  if (typeof path !== 'string' || !path.startsWith('/')) {
    throw new Error('invalid project patch path')
  }
  return path.slice(1).split('/').map(token => token.replace(/~1/g, '/').replace(/~0/g, '~'))
}

function cloneContainer(container) {
  if (Array.isArray(container)) return [...container]
  if (container && typeof container === 'object') return { ...container }
  throw new Error('invalid project patch target')
}

function arrayIndex(token, length, allowAppend = false) {
  if (allowAppend && token === '-') return length
  const index = Number(token)
  if (!Number.isInteger(index) || index < 0 || index > length || (!allowAppend && index >= length)) {
    throw new Error('invalid project patch index')
  }
  return index
}

function applyOperationAt(target, tokens, operation) {
  if (tokens.length === 0) {
    if (operation.op === 'remove') return undefined
    return cloneValue(operation.value)
  }

  const [token, ...rest] = tokens
  const next = cloneContainer(target)

  if (rest.length === 0) {
    if (Array.isArray(next)) {
      if (operation.op === 'add') {
        next.splice(arrayIndex(token, next.length, true), 0, cloneValue(operation.value))
      } else if (operation.op === 'remove') {
        next.splice(arrayIndex(token, next.length), 1)
      } else if (operation.op === 'replace') {
        next[arrayIndex(token, next.length)] = cloneValue(operation.value)
      } else {
        throw new Error('unsupported project patch operation')
      }
      return next
    }
    if (operation.op === 'remove') {
      delete next[token]
    } else if (operation.op === 'add' || operation.op === 'replace') {
      next[token] = cloneValue(operation.value)
    } else {
      throw new Error('unsupported project patch operation')
    }
    return next
  }

  if (Array.isArray(next)) {
    const index = arrayIndex(token, next.length)
    next[index] = applyOperationAt(next[index], rest, operation)
  } else {
    next[token] = applyOperationAt(next[token], rest, operation)
  }
  return next
}

export function applyProjectPatch(project, patch) {
  if (!project || !Array.isArray(patch)) {
    throw new Error('invalid project patch')
  }
  return patch.reduce(
    (nextProject, operation) => applyOperationAt(nextProject, pointerTokens(operation.path), operation),
    project
  )
}

export function mergeProjectBroadcast(currentProject, currentRevision, message) {
  const nextRevision = String(message?.revision || currentRevision || '')
  if (message?.project) {
    return { project: message.project, revision: nextRevision, needsReload: false }
  }
  if (currentRevision && nextRevision && currentRevision === nextRevision) {
    return { project: null, revision: currentRevision, needsReload: false }
  }
  if (!Array.isArray(message?.patch) || !message.base_revision) {
    return { project: null, revision: currentRevision || '', needsReload: true }
  }
  if (!currentProject || String(message.base_revision) !== String(currentRevision || '')) {
    return { project: null, revision: currentRevision || '', needsReload: true }
  }
  return {
    project: applyProjectPatch(currentProject, message.patch),
    revision: nextRevision,
    needsReload: false,
  }
}
