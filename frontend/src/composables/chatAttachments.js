function makeAttachmentId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2)
}

export function normalizeImagePayload(images) {
  return (images || [])
    .map((image) => ({
      dataUrl: image.dataUrl || image.src || image.url || '',
      name: image.name || 'image',
      type: image.type || '',
      size: Number(image.size || 0),
    }))
    .filter((image) => image.dataUrl)
}

export function normalizeFilePayload(files) {
  return (files || [])
    .map((file) => ({
      dataUrl: file.dataUrl || file.url || '',
      name: file.name || 'file',
      type: file.type || '',
      size: Number(file.size || 0),
    }))
    .filter((file) => file.dataUrl)
}

export function normalizeImageAttachments(images) {
  return normalizeImagePayload(images).map((image) => ({
    id: makeAttachmentId(),
    name: image.name,
    type: image.type,
    size: image.size,
    src: image.dataUrl,
  }))
}

export function normalizeFileAttachments(files) {
  return normalizeFilePayload(files).map((file) => ({
    id: makeAttachmentId(),
    kind: 'file',
    name: file.name,
    type: file.type,
    size: file.size,
  }))
}

export function buildUserMessageAttachments(images = [], files = []) {
  return [
    ...normalizeImageAttachments(images),
    ...normalizeFileAttachments(files),
  ]
}
