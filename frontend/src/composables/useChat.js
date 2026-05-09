import { ref } from 'vue'
import { useApi } from './useApi.js'
import { useSession } from './useSession.js'

let instance = null

export function useChat() {
  if (instance) return instance

  const api = useApi()
  const { currentId: sessionId, switchSession, normalizeSessionId } = useSession()

  const messages = ref([])
  const sending = ref(false)
  const tokenInfo = ref(null)

  // Thinking state
  const thinkingText = ref('')
  const thinkingStart = ref(0)
  const thinkingBlock = ref(null) // { content, startTime, done }
  // Tool cards
  const toolCards = ref({}) // id -> { tool, args, status: 'executing'|'success'|'failed', result }
  const toolMessageIndex = new Map()
  let streamingAssistantId = null
  let streamingMessage = null

  // WebSocket event handler — called from ChatPage
  function handleWsEvent(msg) {
    if (msg.type === 'thinking') {
      startThinkingBlock()
    }
    if (msg.type === 'thinking_delta') {
      if (!thinkingBlock.value) {
        startThinkingBlock()
      } else if (thinkingBlock.value.done && (thinkingBlock.value.content || '').trim()) {
        startThinkingBlock()
      }
      thinkingBlock.value.content += msg.content || ''
    }
    if (msg.type === 'thinking_done') {
      if (thinkingBlock.value) {
        thinkingBlock.value.done = true
      }
    }
    if (msg.type === 'response_start') {
      ensureAssistantStream()
    }
    if (msg.type === 'response_delta') {
      appendAssistantDelta(msg.content || '')
    }
    if (msg.type === 'response_done') {
      finishAssistantStream(msg.content || '')
    }
    if (msg.type === 'tool_start') {
      finishAssistantStream()
      addToolMessage(msg.data.id, {
        tool: msg.data.tool,
        args: msg.data.args,
        status: 'executing',
        result: null,
      })
      toolCards.value = {
        ...toolCards.value,
        [msg.data.id]: {
          tool: msg.data.tool,
          args: msg.data.args,
          status: 'executing',
          result: null,
        },
      }
    }
    if (msg.type === 'tool_end') {
      updateToolMessage(msg.data.id, {
        tool: msg.data.tool,
        args: msg.data.args,
        status: msg.data.success ? 'success' : 'failed',
        result: msg.data.result_preview || null,
        resultCompressed: Boolean(msg.data.result_compressed),
        resultId: msg.data.result_id || '',
      })
      toolCards.value = {
        ...toolCards.value,
        [msg.data.id]: {
          ...toolCards.value[msg.data.id],
          tool: msg.data.tool,
          args: msg.data.args,
          status: msg.data.success ? 'success' : 'failed',
          result: msg.data.result_preview || null,
          resultCompressed: Boolean(msg.data.result_compressed),
          resultId: msg.data.result_id || '',
        },
      }
    }
  }

  function clearThinking() {
    thinkingText.value = ''
    thinkingStart.value = 0
    thinkingBlock.value = null
  }

  function clearToolCards() {
    toolCards.value = {}
  }

  function makeId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2)
  }

  function addMessage(role, content, md = false, extra = {}) {
    messages.value.push({
      id: makeId(),
      role,
      content,
      md,
      time: new Date(),
      ...extra,
    })
  }

  function startThinkingBlock() {
    const block = {
      id: makeId(),
      role: 'thinking',
      content: '',
      startTime: Date.now(),
      done: false,
      time: new Date(),
    }
    thinkingBlock.value = block
    messages.value.push(block)
  }

  function findMessageIndex(id) {
    return messages.value.findIndex((m) => m.id === id)
  }

  function patchMessage(id, patch) {
    const index = findMessageIndex(id)
    if (index < 0) return null
    const next = { ...messages.value[index], ...patch }
    messages.value.splice(index, 1, next)
    return next
  }

  function ensureAssistantStream() {
    if (streamingAssistantId && streamingMessage) {
      return streamingMessage
    }

    streamingAssistantId = makeId()
    const message = {
      id: streamingAssistantId,
      role: 'assistant',
      content: '',
      md: true,
      streaming: true,
      time: new Date(),
    }
    messages.value.push(message)
    streamingMessage = messages.value[messages.value.length - 1]
    return streamingMessage
  }

  function appendAssistantDelta(delta) {
    if (!delta) return
    const msg = ensureAssistantStream()
    streamingMessage = patchMessage(msg.id, {
      content: (msg.content || '') + delta,
      streaming: true,
    })
  }

  function finishAssistantStream(finalContent = '') {
    if (!streamingAssistantId || !streamingMessage) {
      if (finalContent) addMessage('assistant', finalContent, true)
      return
    }

    streamingMessage = patchMessage(streamingAssistantId, {
      content: finalContent || streamingMessage.content,
      streaming: false,
    })
    streamingMessage = null
    streamingAssistantId = null
  }

  function addToolMessage(toolCallId, toolData) {
    const existing = toolMessageIndex.get(toolCallId)
    if (existing !== undefined && messages.value[existing]) {
      messages.value[existing].toolData = {
        ...messages.value[existing].toolData,
        ...toolData,
      }
      return
    }

    messages.value.push({
      id: toolCallId || makeId(),
      role: 'tool',
      toolCallId,
      toolData,
      time: new Date(),
    })
    toolMessageIndex.set(toolCallId, messages.value.length - 1)
  }

  function updateToolMessage(toolCallId, patch) {
    const existing = toolMessageIndex.get(toolCallId)
    if (existing === undefined || !messages.value[existing]) {
      addToolMessage(toolCallId, patch)
      return
    }

    messages.value[existing].toolData = {
      ...messages.value[existing].toolData,
      ...patch,
    }
  }

  function resetMessages() {
    messages.value = []
    toolMessageIndex.clear()
    streamingAssistantId = null
    streamingMessage = null
  }

  function parseToolArgs(raw) {
    if (!raw) return {}
    if (typeof raw === 'object') return raw
    try {
      return JSON.parse(raw)
    } catch {
      return {}
    }
  }

  function loadTranscript(rawMessages) {
    resetMessages()
    const callsById = new Map()

    rawMessages.forEach((m) => {
      if (m.tool_calls?.length) {
        m.tool_calls.forEach((call) => {
          callsById.set(call.id, {
            tool: call.function?.name || call.name || 'tool',
            args: parseToolArgs(call.function?.arguments || call.arguments),
          })
        })
      }

      if (m.role === 'user' && m.content) {
        const parsed = parseUserContent(m.content)
        addMessage('user', parsed.text, false, { attachments: parsed.attachments })
      } else if (m.role === 'assistant' && m.content) {
        addMessage('assistant', m.content, true)
      } else if (m.role === 'tool') {
        const call = callsById.get(m.tool_call_id) || {}
        const result = m.content || ''
        addToolMessage(m.tool_call_id, {
          tool: call.tool || 'tool',
          args: call.args || {},
          status: result.startsWith('Error') ? 'failed' : 'success',
          result,
          resultCompressed: result.startsWith('<persisted-output>'),
          resultId: extractToolResultId(result),
        })
      }
    })
  }

  function extractToolResultId(result) {
    if (!result) return ''
    const match = String(result).match(/^(?:tool_result_id|Tool result id):\s*(\S+)/m)
    return match ? match[1] : ''
  }

  function parseUserContent(content) {
    if (typeof content === 'string') {
      return { text: content, attachments: [] }
    }
    if (!Array.isArray(content)) {
      return { text: String(content || ''), attachments: [] }
    }

    const textParts = []
    const attachments = []
    content.forEach((part, index) => {
      if (typeof part === 'string') {
        textParts.push(part)
        return
      }
      if (!part || typeof part !== 'object') return
      if (part.type === 'text' && typeof part.text === 'string') {
        textParts.push(part.text)
        return
      }
      if (part.type === 'image_url') {
        const src = typeof part.image_url === 'string' ? part.image_url : part.image_url?.url
        if (src) {
          attachments.push({
            id: makeId(),
            name: part.name || `image-${index + 1}`,
            type: mimeFromDataUrl(src),
            size: 0,
            src,
          })
        }
        return
      }
      if (part.type === 'image' && part.source?.type === 'base64') {
        const mediaType = part.source.media_type || 'image/png'
        attachments.push({
          id: makeId(),
          name: part.name || `image-${index + 1}`,
          type: mediaType,
          size: 0,
          src: `data:${mediaType};base64,${part.source.data || ''}`,
        })
      }
    })

    return { text: textParts.join('').trim(), attachments }
  }

  function mimeFromDataUrl(src) {
    const match = String(src || '').match(/^data:([^;,]+)[;,]/)
    return match ? match[1] : ''
  }

  function normalizeImagePayload(images) {
    return (images || [])
      .map((image) => ({
        dataUrl: image.dataUrl || image.src || image.url || '',
        name: image.name || 'image',
        type: image.type || '',
        size: Number(image.size || 0),
      }))
      .filter((image) => image.dataUrl)
  }

  function normalizeImageAttachments(images) {
    return normalizeImagePayload(images).map((image) => ({
      id: makeId(),
      name: image.name,
      type: image.type,
      size: image.size,
      src: image.dataUrl,
    }))
  }

  async function cancelMessage() {
    if (!sending.value) return
    try {
      await api.cancelChat(sessionId.value)
    } catch {
      // best-effort
    }
  }

  async function sendMessage(text, images = []) {
    const messageText = String(text || '')
    const imagePayload = normalizeImagePayload(images)
    if ((!messageText.trim() && !imagePayload.length) || sending.value) return
    sending.value = true
    clearThinking()
    clearToolCards()
    streamingAssistantId = null
    streamingMessage = null

    addMessage('user', messageText, false, { attachments: normalizeImageAttachments(imagePayload) })

    try {
      const result = await api.sendMessage(messageText, sessionId.value, imagePayload)

      if (result.session_id) {
        const newId = normalizeSessionId(result.session_id)
        if (newId !== sessionId.value) {
          await switchSession(newId)
        }
      }

      if (result.error) {
        addMessage('assistant', `Error: ${result.error}`, false)
      } else if (!streamingAssistantId && !messages.value.some((m) => m.role === 'assistant' && m.streaming === false && m.content === result.response)) {
        await new Promise(r => setTimeout(r, 120))
        if (!streamingAssistantId) {
          addMessage('assistant', result.response, true)
        }
      }

      if (!result.error) {
        if (result.token_usage) {
          tokenInfo.value = result.token_usage
        }
      }
    } catch (e) {
      addMessage('assistant', `Connection error: ${e.message}`, false)
    }

    sending.value = false
    clearThinking()
    clearToolCards()
  }

  instance = {
    messages,
    sending,
    tokenInfo,
    thinkingBlock,
    toolCards,
    handleWsEvent,
    clearThinking,
    clearToolCards,
    addMessage,
    addToolMessage,
    updateToolMessage,
    resetMessages,
    loadTranscript,
    sendMessage,
    cancelMessage,
  }
  return instance
}

export function clearChatInstance() {
  instance = null
}
