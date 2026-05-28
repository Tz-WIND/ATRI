const CONTEXT_TOOL_NAMES = new Set(['read_file', 'list_dir', 'tree', 'glob', 'grep', 'search'])

export function buildChatDisplayItems(messages = []) {
  const items = []
  let contextTools = []

  function flushContextTools() {
    if (!contextTools.length) return
    const first = contextTools[0]
    const last = contextTools[contextTools.length - 1]
    items.push({
      id: `context-${first.id}-${last.id}`,
      type: 'tool-group',
      tools: contextTools.map((message) => ({
        id: message.id,
        ...(message.toolData || {}),
      })),
    })
    contextTools = []
  }

  messages.forEach((message) => {
    if (message.role === 'tool' && CONTEXT_TOOL_NAMES.has(message.toolData?.tool)) {
      contextTools.push(message)
      return
    }

    flushContextTools()
    items.push({
      id: message.id,
      type: message.role,
      message,
    })
  })

  flushContextTools()
  return items
}
