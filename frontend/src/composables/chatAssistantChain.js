function fallbackId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2)
}

function mimeFromDataUrl(src) {
  const match = String(src || '').match(/^data:([^;,]+)[;,]/)
  return match ? match[1] : ''
}

export function normalizeAssistantChain(chain, fallbackText = '', idFactory = fallbackId) {
  if (!Array.isArray(chain)) {
    return { text: String(fallbackText || ''), attachments: [] }
  }

  const textParts = []
  const attachments = []
  chain.forEach((part, index) => {
    if (!part || typeof part !== 'object') return
    if (part.type === 'plain') {
      textParts.push(part.text || '')
      return
    }
    if (part.type !== 'image') return

    const src = part.url || ''
    if (!src) return
    attachments.push({
      id: idFactory(),
      name: part.file || `generated-${index + 1}`,
      type: part.mime_type || mimeFromDataUrl(src),
      size: Number(part.size || 0),
      src,
    })
  })

  return {
    text: textParts.join('\n').trim() || String(fallbackText || ''),
    attachments,
  }
}
