import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const studioSource = readFileSync(new URL('../MusicStudio.vue', import.meta.url), 'utf8')
const dialogUrl = new URL('./AutomationParameterPickerDialog.vue', import.meta.url)
const dialogSource = existsSync(dialogUrl) ? readFileSync(dialogUrl, 'utf8') : ''

test('musicStudio_extractsAutomationParameterPickerDialog', () => {
  assert.ok(existsSync(dialogUrl), 'AutomationParameterPickerDialog component should exist')
  assert.match(studioSource, /import AutomationParameterPickerDialog from '\.\/studio\/AutomationParameterPickerDialog\.vue'/)
  assert.match(studioSource, /<AutomationParameterPickerDialog/)
  assert.doesNotMatch(studioSource, /class="automation-parameter-dialog"/)
})

test('automationParameterPickerDialog_ownsModalMarkupAndEvents', () => {
  assert.match(dialogSource, /class="modal-backdrop automation-parameter-backdrop"/)
  assert.match(dialogSource, /class="automation-parameter-dialog"/)
  assert.match(dialogSource, /v-for="target in defaultTargets"/)
  assert.match(dialogSource, /v-for="item in learnedTargets"/)
  assert.match(dialogSource, /emit\('bind-target', target\.target\)/)
  assert.match(dialogSource, /emit\('refresh-captured'\)/)
  assert.match(dialogSource, /emit\('rename-learned-target', item\.id, \$event\.target\.value\)/)
  assert.match(dialogSource, /emit\('close'\)/)
})

test('automationParameterPickerDialog_keepsDialogStylesWithComponent', () => {
  assert.match(dialogSource, /\.automation-parameter-dialog\s*\{/)
  assert.match(dialogSource, /\.automation-learned-row input\s*\{/)
  assert.match(dialogSource, /@media \(max-width: 1120px\)/)
  assert.doesNotMatch(studioSource, /\.automation-parameter-dialog\s*\{/)
  assert.doesNotMatch(studioSource, /\.automation-learned-row input\s*\{/)
})
