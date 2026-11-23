/**
 * Tests for the desktop-size selector values and payload.
 *
 *   node tests/desktopSizes.test.mjs
 */
import assert from 'node:assert/strict'
import {
  AUTO_DESKTOP_SIZE,
  CUSTOM_DESKTOP_SIZE,
  desktopSizeId,
  desktopSizeOptions,
  desktopSizePayload,
  validateDesktopSize
} from '../src/utils/desktopSizes.js'

let failed = 0

async function test(name, fn) {
  try {
    await fn()
    console.log(`ok - ${name}`)
  } catch (err) {
    failed++
    console.error(`not ok - ${name}`)
    console.error(`  ${err.message}`)
  }
}

await test('auto is the first and default option', () => {
  const options = desktopSizeOptions()
  assert.equal(options[0].id, AUTO_DESKTOP_SIZE.id)
  assert.equal(options[0].auto, true)
  assert.equal(options[options.length - 1].id, CUSTOM_DESKTOP_SIZE.id)
  assert.ok(options.some(option => option.id === '1920x1080'))
})

await test('the backend presets win when provided', () => {
  const options = desktopSizeOptions([
    { id: 'auto', label: 'Auto', auto: true },
    { id: '800x600', label: '800 × 600', width: 800, height: 600 },
    { id: 'custom', label: 'Custom…', custom: true }
  ])
  assert.equal(options.length, 3)
  assert.equal(options[1].id, '800x600')
})

await test('stored values map back to the selected option', () => {
  assert.equal(desktopSizeId({ auto: true }), 'auto')
  assert.equal(desktopSizeId({ auto: false, width: 1920, height: 1080 }), '1920x1080')
  assert.equal(desktopSizeId({ auto: false, width: 1234, height: 567 }), 'custom')
})

await test('payload defaults to client size and keeps a fallback', () => {
  assert.deepEqual(desktopSizePayload({ auto: true }), {
    desktop_auto_resize: true,
    desktop_width: null,
    desktop_height: null
  })
  assert.deepEqual(desktopSizePayload({ auto: true, width: 1600, height: 900 }), {
    desktop_auto_resize: true,
    desktop_width: 1600,
    desktop_height: 900
  })
  assert.deepEqual(desktopSizePayload({ auto: false }), {
    desktop_auto_resize: false,
    desktop_width: 1920,
    desktop_height: 1080
  })
})

await test('manual sizes are validated', () => {
  assert.equal(validateDesktopSize({ auto: true }), null)
  assert.equal(validateDesktopSize({ auto: false, width: 1280, height: 720 }), null)
  assert.ok(validateDesktopSize({ auto: false, width: 100, height: 720 }))
  assert.ok(validateDesktopSize({ auto: false, width: 1280, height: 100 }))
})

if (failed) {
  console.error(`\n${failed} test(s) failed`)
  process.exit(1)
}
console.log('\nall desktop size tests passed')
process.exit(0)
