function directoryTrustPaths(error) {
  const paths = error?.body?.paths
  if (!Array.isArray(paths)) return []
  return paths.map(path => String(path || '').trim()).filter(Boolean)
}

export function directoryTrustMessage(error, options = {}) {
  const title = options.title || 'Trust these directories?'
  const description = options.description || 'Confirm before expanding file access.'
  return [
    title,
    '',
    ...directoryTrustPaths(error),
    '',
    description,
  ].join('\n')
}

function defaultConfirm(message) {
  const target = globalThis.window || globalThis
  const confirm = target.confirm
  return typeof confirm === 'function' ? confirm.call(target, message) : false
}

export async function retryAfterDirectoryTrust(error, retry, options = {}) {
  if (!error?.body?.requires_trust) throw error

  const confirm = typeof options.confirm === 'function' ? options.confirm : defaultConfirm
  if (!confirm(directoryTrustMessage(error, options))) return false

  await retry()
  return true
}
