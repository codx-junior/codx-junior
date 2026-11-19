/**
 * Tests for the shared language resolution (voice call + settings).
 *
 *   node tests/voiceLanguage.test.mjs
 */
import assert from 'node:assert/strict'
import {
  languageLabel,
  normalizeBrowserLocale,
  resolveSpeechLanguage
} from '../src/config/languages.js'

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

await test('an explicit user language wins over the browser', () => {
  assert.equal(
    resolveSpeechLanguage({ userLanguage: 'es', browserLanguage: 'en-US' }),
    'es-ES'
  )
  assert.equal(
    resolveSpeechLanguage({ userLanguage: 'ja', browserLanguage: 'en-US' }),
    'ja-JP'
  )
})

await test('a base language keeps a matching browser region', () => {
  assert.equal(
    resolveSpeechLanguage({ userLanguage: 'es', browserLanguage: 'es-MX' }),
    'es-MX'
  )
  assert.equal(
    resolveSpeechLanguage({ userLanguage: 'pt', browserLanguage: 'pt-PT' }),
    'pt-PT'
  )
})

await test('an explicit region is preserved', () => {
  assert.equal(
    resolveSpeechLanguage({ userLanguage: 'pt-BR', browserLanguage: 'pt-PT' }),
    'pt-BR'
  )
})

await test('automatic or missing uses the browser locale', () => {
  assert.equal(
    resolveSpeechLanguage({ userLanguage: 'auto', browserLanguage: 'fr-CA' }),
    'fr-CA'
  )
  assert.equal(
    resolveSpeechLanguage({ userLanguage: '', browserLanguage: 'de-DE' }),
    'de-DE'
  )
})

await test('falls back safely when neither is present', () => {
  assert.equal(resolveSpeechLanguage({}), 'en-US')
  assert.equal(
    resolveSpeechLanguage({ fallback: 'es-SP' }),
    'es-ES'
  )
})

await test('normalises browser locale spellings', () => {
  assert.equal(normalizeBrowserLocale('es_mx'), 'es-MX')
  assert.equal(normalizeBrowserLocale('EN-us'), 'en-US')
  assert.equal(normalizeBrowserLocale('pt-br'), 'pt-BR')
  assert.equal(normalizeBrowserLocale('not a locale'), '')
})

await test('labels are human readable for codes and locales', () => {
  assert.equal(languageLabel('auto'), 'Automatic (match my message)')
  assert.equal(languageLabel('es'), 'Español')
  assert.equal(languageLabel('es-ES'), 'Español (es-ES)')
  assert.equal(languageLabel('es-MX'), 'Español (es-MX)')
})

if (failed) {
  console.error(`\n${failed} language test(s) failed`)
  process.exit(1)
}
