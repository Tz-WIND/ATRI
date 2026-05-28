export const HTTP_ASSISTANT_RESPONSE_DEDUPE_DELAY_MS = 120

function messageList(messagesRef) {
  if (Array.isArray(messagesRef?.value)) return messagesRef.value
  return Array.isArray(messagesRef) ? messagesRef : []
}

export function hasActiveAssistantStream(messagesRef) {
  return messageList(messagesRef).some((message) =>
    message?.role === 'assistant' && message.streaming
  )
}

export function hasAssistantResponse(messagesRef, response) {
  const text = String(response || '')
  if (!text) return false
  return messageList(messagesRef).some((message) =>
    message?.role === 'assistant'
    && message.streaming !== true
    && String(message.content || '') === text
  )
}

export async function shouldAppendHttpAssistantResponse(
  messagesRef,
  response,
  delayMs = HTTP_ASSISTANT_RESPONSE_DEDUPE_DELAY_MS,
) {
  const text = String(response || '')
  if (!text) return false
  if (hasActiveAssistantStream(messagesRef) || hasAssistantResponse(messagesRef, text)) {
    return false
  }

  await new Promise(resolve => setTimeout(resolve, Math.max(0, Number(delayMs) || 0)))
  return !hasActiveAssistantStream(messagesRef) && !hasAssistantResponse(messagesRef, text)
}
