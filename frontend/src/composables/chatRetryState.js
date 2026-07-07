export function createChatRetryState({
  getMessages,
  isSending = () => false,
  clearErrors = () => {},
  addUserMessage,
} = {}) {
  let lastEntry = null

  function currentMessages() {
    return typeof getMessages === 'function' ? getMessages() : []
  }

  function userMessageId(message) {
    return message?.id ? String(message.id) : ''
  }

  function hasUserMessageId(messageId) {
    if (!messageId) return false
    const list = currentMessages()
    if (!Array.isArray(list)) return false
    return list.some((message) => message.role === 'user' && message.id === messageId)
  }

  function trackUserMessage(payload) {
    if (typeof addUserMessage !== 'function') return ''
    return userMessageId(addUserMessage(payload))
  }

  function beginFreshSend(payload) {
    clearErrors()
    lastEntry = {
      payload,
      userMessageId: trackUserMessage(payload),
    }
    return payload
  }

  function beginRetry() {
    if (!canRetry()) return null
    clearErrors()
    if (!hasUserMessageId(lastEntry.userMessageId)) {
      lastEntry.userMessageId = trackUserMessage(lastEntry.payload)
    }
    return lastEntry.payload
  }

  function canRetry() {
    return Boolean(lastEntry) && !isSending()
  }

  function getLastPayload() {
    return lastEntry?.payload ?? null
  }

  function reset() {
    lastEntry = null
  }

  return {
    beginFreshSend,
    beginRetry,
    canRetry,
    getLastPayload,
    reset,
  }
}
