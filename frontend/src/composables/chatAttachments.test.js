import assert from 'node:assert/strict'

import {
  buildUserMessageAttachments,
  normalizeFileAttachments,
  normalizeFilePayload,
  normalizeImageAttachments,
  normalizeImagePayload,
} from './chatAttachments.js'

const image = {
  dataUrl: 'data:image/png;base64,abc',
  name: 'diagram.png',
  type: 'image/png',
  size: 12,
}

const file = {
  dataUrl: 'data:text/plain;base64,Zm9v',
  name: 'notes.txt',
  type: 'text/plain',
  size: 3,
}

assert.deepEqual(normalizeImagePayload([image, { name: 'missing' }]), [image])
assert.deepEqual(normalizeFilePayload([file, { name: 'empty' }]), [file])

const imageAttachment = normalizeImageAttachments([image])[0]
assert.equal(imageAttachment.name, 'diagram.png')
assert.equal(imageAttachment.src, image.dataUrl)
assert.ok(imageAttachment.id)

const fileAttachment = normalizeFileAttachments([file])[0]
assert.equal(fileAttachment.kind, 'file')
assert.equal(fileAttachment.name, 'notes.txt')
assert.ok(fileAttachment.id)

const attachments = buildUserMessageAttachments([image], [file])
assert.equal(attachments.length, 2)
assert.equal(attachments[0].src, image.dataUrl)
assert.equal(attachments[1].kind, 'file')
