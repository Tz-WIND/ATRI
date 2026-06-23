import assert from 'node:assert/strict'
import test from 'node:test'
import { numberInputModelValue } from './numericInputValue.js'

test('numberInputModelValue_preservesEmptyInputInsteadOfCoercingToZero', () => {
  assert.equal(numberInputModelValue(''), '')
  assert.equal(numberInputModelValue('  '), '  ')
})

test('numberInputModelValue_castsValidNumericInput', () => {
  assert.equal(numberInputModelValue('120'), 120)
  assert.equal(numberInputModelValue('96.5'), 96.5)
  assert.equal(numberInputModelValue('0'), 0)
})
