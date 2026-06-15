import assert from 'node:assert/strict'
import test from 'node:test'

import { retryAfterDirectoryTrust } from './directoryTrust.js'

const TRUST_OPTIONS = {
  title: 'Trust these music directories?',
  description: 'ATRI will be able to scan these directories.',
}

test('retryAfterDirectoryTrust confirms untrusted paths and retries the trusted save', async () => {
  const calls = []
  const error = {
    body: {
      requires_trust: true,
      paths: ['D:\\Music'],
    },
  }

  const retried = await retryAfterDirectoryTrust(
    error,
    async () => {
      calls.push({ type: 'retry', trust: true })
    },
    {
      ...TRUST_OPTIONS,
      confirm(message) {
        calls.push({ type: 'confirm', message })
        return true
      },
    },
  )

  assert.equal(retried, true)
  assert.deepEqual(calls, [
    {
      type: 'confirm',
      message: [
        'Trust these music directories?',
        '',
        'D:\\Music',
        '',
        'ATRI will be able to scan these directories.',
      ].join('\n'),
    },
    { type: 'retry', trust: true },
  ])
})

test('retryAfterDirectoryTrust stops without retrying when confirmation is declined', async () => {
  let retryCalled = false

  const retried = await retryAfterDirectoryTrust(
    { body: { requires_trust: true, paths: ['D:\\Music'] } },
    async () => {
      retryCalled = true
    },
    {
      ...TRUST_OPTIONS,
      confirm() {
        return false
      },
    },
  )

  assert.equal(retried, false)
  assert.equal(retryCalled, false)
})

test('retryAfterDirectoryTrust rethrows errors that do not request directory trust', async () => {
  const error = new Error('network failed')

  await assert.rejects(
    () => retryAfterDirectoryTrust(error, async () => {}, {
      ...TRUST_OPTIONS,
      confirm() {
        return true
      },
    }),
    thrown => thrown === error,
  )
})
