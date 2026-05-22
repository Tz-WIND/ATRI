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
  const todoSnapshot = ref(emptyTodoSnapshot())

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
      finishThinkingBlock()
    }
    if (msg.type === 'response_start') {
      finishThinkingBlock()
      ensureAssistantStream()
    }
    if (msg.type === 'response_delta') {
      finishThinkingBlock()
      appendAssistantDelta(msg.content || '')
    }
    if (msg.type === 'response_done') {
      finishThinkingBlock()
      finishAssistantStream(msg.content || '')
    }
    if (msg.type === 'todo_snapshot') {
      todoSnapshot.value = normalizeTodoSnapshot(msg.todo || msg.todo_snapshot || msg)
      finishThinkingBlock()
      finishAssistantStream()
      addTodoMessage(todoSnapshot.value)
    }
    if (msg.type === 'tool_start') {
      finishThinkingBlock()
      finishAssistantStream()
      if (msg.data.tool === 'todo') return
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
      finishThinkingBlock()
      if (msg.data.tool === 'todo') return
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
    finishThinkingBlock()
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

  function addOrPatchAssistantMessage(content, attachments = []) {
    const text = String(content || '')
    const lastIndexFromEnd = [...messages.value].reverse().findIndex((m) =>
      m.role === 'assistant' && !m.streaming && String(m.content || '') === text
    )
    if (lastIndexFromEnd >= 0) {
      const index = messages.value.length - 1 - lastIndexFromEnd
      const current = messages.value[index]
      messages.value.splice(index, 1, {
        ...current,
        attachments: mergeAttachments(current.attachments, attachments),
      })
      return
    }
    addMessage('assistant', text, true, { attachments })
  }

  function startThinkingBlock() {
    finishThinkingBlock()
    const now = Date.now()
    const block = {
      id: makeId(),
      role: 'thinking',
      content: '',
      startTime: now,
      endTime: null,
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

  function finishThinkingBlock() {
    const current = thinkingBlock.value
    if (!current || current.done) return
    const endTime = Date.now()
    const patched = patchMessage(current.id, { done: true, endTime })
    thinkingBlock.value = patched || { ...current, done: true, endTime }
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
    todoSnapshot.value = emptyTodoSnapshot()
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

  function loadTranscript(transcript) {
    resetMessages()
    const callsById = new Map()
    const rawMessages = Array.isArray(transcript) ? transcript : transcript?.messages || []
    const runtimeTurns = Array.isArray(transcript?.runtimeTurns) ? transcript.runtimeTurns : []
    const runtimeItems = Array.isArray(transcript?.runtimeItems) ? transcript.runtimeItems : []
    todoSnapshot.value = normalizeTodoSnapshot(transcript?.todoSnapshot)
    const reasoningByTurn = new Map()

    runtimeItems
      .filter((item) => item?.kind === 'agent_reasoning' && String(item.detail || '').trim())
      .forEach((item) => {
        const list = reasoningByTurn.get(item.turn_id) || []
        list.push(item)
        reasoningByTurn.set(item.turn_id, list)
      })

    const orderedTurnIds = runtimeTurns
      .map((turn) => turn?.id)
      .filter(Boolean)
      .filter((turnId) => reasoningByTurn.has(turnId))
    const fallbackReasoning = runtimeItems
      .filter((item) => item?.kind === 'agent_reasoning' && String(item.detail || '').trim())
      .filter((item) => !item.turn_id || !orderedTurnIds.includes(item.turn_id))
    let turnIndex = 0

    function addRuntimeThinkingForNextTurn() {
      let items = []
      while (turnIndex < orderedTurnIds.length && !items.length) {
        const turnId = orderedTurnIds[turnIndex]
        turnIndex += 1
        items = reasoningByTurn.get(turnId) || []
      }
      if (!items.length && fallbackReasoning.length) {
        items = [fallbackReasoning.shift()]
      }
      items.forEach(addRuntimeThinkingMessage)
    }

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
        // Tool-result user messages are continuations of the current turn,
        // not new user requests -- skip inserting runtime thinking here.
        const isToolResult = Array.isArray(m.content) && m.content.some(part => part?.type === 'tool_result')
        if (!isToolResult) {
          addRuntimeThinkingForNextTurn()
        }
      } else if (m.role === 'assistant' && m.content) {
        addMessage('assistant', m.content, true, {
          attachments: normalizeStoredAttachments(m._atri_attachments),
        })
      } else if (m.role === 'tool') {
        const call = callsById.get(m.tool_call_id) || {}
        if (call.tool === 'todo') {
          return
        }
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

    while (turnIndex < orderedTurnIds.length) {
      addRuntimeThinkingForNextTurn()
    }
    fallbackReasoning.forEach(addRuntimeThinkingMessage)
    addTodoMessage(todoSnapshot.value)
  }

  function addRuntimeThinkingMessage(item) {
    const content = String(item.detail || '').trim()
    if (!content) return
    const startTime = parseRuntimeTime(item.started_at || item.created_at) || Date.now()
    const endTime = parseRuntimeTime(item.ended_at) || startTime
    messages.value.push({
      id: item.id || makeId(),
      role: 'thinking',
      content,
      startTime,
      endTime,
      done: true,
      time: new Date(startTime),
    })
  }

  function parseRuntimeTime(value) {
    if (!value) return 0
    const parsed = Date.parse(String(value))
    return Number.isNaN(parsed) ? 0 : parsed
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

  function normalizeAssistantChain(chain, fallbackText = '') {
    if (!Array.isArray(chain)) {
      return { text: String(fallbackText || ''), attachments: [] }
    }
    const textParts = []
    const attachments = []
    chain.forEach((part, index) => {
      if (!part || typeof part !== 'object') return
      if (part.type === 'plain') {
        textParts.push(part.text || '')
      } else if (part.type === 'image') {
        const src = part.url || ''
        if (src) {
          attachments.push({
            id: makeId(),
            name: part.file || `generated-${index + 1}`,
            type: part.mime_type || mimeFromDataUrl(src),
            size: Number(part.size || 0),
            src,
          })
        }
      }
    })
    return { text: textParts.join('\n').trim() || String(fallbackText || ''), attachments }
  }

  function normalizeStoredAttachments(rawAttachments) {
    if (!Array.isArray(rawAttachments)) return []
    return rawAttachments
      .map((image, index) => ({
        id: makeId(),
        name: image.name || `generated-${index + 1}`,
        type: image.type || mimeFromDataUrl(image.src),
        size: Number(image.size || 0),
        src: image.src || image.url || '',
      }))
      .filter((image) => image.src)
  }

  function mergeAttachments(existing = [], incoming = []) {
    const seen = new Set()
    return [...existing, ...incoming].filter((image) => {
      const key = image.src || image.id || image.name
      if (!key || seen.has(key)) return false
      seen.add(key)
      return true
    })
  }

  function emptyTodoSnapshot() {
    return {
      items: [],
      total: 0,
      completed: 0,
      all_completed: false,
      updated_at: '',
      session_id: '',
    }
  }

  function normalizeTodoSnapshot(raw) {
    if (!raw || typeof raw !== 'object') return emptyTodoSnapshot()
    const items = Array.isArray(raw.items)
      ? raw.items
        .map((item, index) => ({
          id: String(item?.id || `todo-${index + 1}`),
          content: String(item?.content || item?.title || item?.text || '').trim(),
          status: item?.status === 'completed' ? 'completed' : 'pending',
        }))
        .filter((item) => item.content)
      : []
    const completed = items.filter((item) => item.status === 'completed').length
    return {
      items,
      total: Number(raw.total ?? items.length),
      completed: Number(raw.completed ?? completed),
      all_completed: Boolean(raw.all_completed ?? (items.length > 0 && completed === items.length)),
      updated_at: String(raw.updated_at || ''),
      session_id: String(raw.session_id || ''),
    }
  }

  function addTodoMessage(snapshot) {
    if (!snapshot?.items?.length) return
    const last = messages.value[messages.value.length - 1]
    if (last?.role === 'todo') {
      patchMessage(last.id, {
        todoSnapshot: snapshot,
        time: new Date(),
      })
      return
    }
    messages.value.push({
      id: makeId(),
      role: 'todo',
      todoSnapshot: snapshot,
      time: new Date(),
    })
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
      } else if (Array.isArray(result.chain)) {
        const parsed = normalizeAssistantChain(result.chain, result.response)
        addOrPatchAssistantMessage(parsed.text, parsed.attachments)
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
    todoSnapshot,
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
