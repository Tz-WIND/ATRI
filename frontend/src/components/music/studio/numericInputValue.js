export function numberInputModelValue(value) {
  const numericValue = Number.parseFloat(value)
  return Number.isNaN(numericValue) ? value : numericValue
}
