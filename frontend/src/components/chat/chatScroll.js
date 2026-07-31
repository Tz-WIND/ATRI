export function resolveAutoScroll(element, current, programmatic, threshold = 60) {
  if (!element) return Boolean(current)
  const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight
  const isNearBottom = distanceFromBottom < threshold
  if (programmatic && isNearBottom) return Boolean(current)
  return isNearBottom
}

export function stickChatToBottom(element, enabled = true) {
  if (!element || !enabled) return false
  const target = Math.max(0, element.scrollHeight - element.clientHeight)
  if (Math.abs(element.scrollTop - target) < 2) return false
  element.scrollTop = target
  return true
}
