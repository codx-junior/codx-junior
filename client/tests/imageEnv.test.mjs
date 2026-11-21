/**
 * Contract tests for the coworking image ENV preset (`src/utils/imageEnv.js`).
 *
 * These pin the rules the two UI consumers rely on:
 *
 *   - `CoworkingImageGallery.vue` renders the "N env" badge and its tooltip
 *     from `declaredEnv()` over a gallery card (`{ coworking_image: { env } }`);
 *   - `BrowserSessionCreateModal.vue` builds its "Image settings" form from the
 *     same list, fills it with `presetDefaults()`, overlays an `.env` file with
 *     `parseDotEnv()` + `mapDotEnv()`, sends `presetValues()` as `extra_env` and
 *     blocks creation with `missingRequired()`.
 *
 * Precedence under test, lowest first: manifest default < `.env` < typed value.
 * Every credential below is a placeholder string, never a real key.
 *
 * Dependency free on purpose (no test runner is installed), run with:
 *
 *   node --test tests/imageEnv.test.mjs
 */
import assert from 'node:assert/strict'
import {
  declaredEnv,
  mapDotEnv,
  missingRequired,
  parseDotEnv,
  presetDefaults,
  presetValues,
  unquote
} from '../src/utils/imageEnv.js'

let failed = 0
const pending = []

function test(name, fn) {
  const ok = () => console.log(`ok - ${name}`)
  const ko = (err) => {
    failed++
    console.error(`not ok - ${name}`)
    console.error(`  ${err.message}`)
  }
  try {
    const result = fn()
    if (result && typeof result.then === 'function') {
      pending.push(result.then(ok, ko))
      return
    }
    ok()
  } catch (err) {
    ko(err)
  }
}

/** The manifest shipped by `.codx/coworking/images/bu01.browser-use/image.json`. */
const BROWSER_USE_ENV = [
  { name: 'OPENAI_API_KEY', label: 'OpenAI API key', required: false, secret: true },
  { name: 'ANTHROPIC_API_KEY', label: 'Anthropic API key', required: false, secret: true },
  {
    name: 'BROWSER_USE_LLM_MODEL',
    label: 'LLM model',
    default: 'gpt-4o-mini',
    required: false,
    secret: false
  },
  {
    name: 'BROWSER_USE_HEADLESS',
    label: 'Headless',
    default: 'false',
    choices: ['false', 'true'],
    required: false,
    secret: false
  },
  {
    name: 'BROWSER_USE_LOGGING_LEVEL',
    label: 'Log level',
    default: 'info',
    choices: ['debug', 'info', 'warning', 'error'],
    required: false,
    secret: false
  }
]

// ── declaredEnv ────────────────────────────────────────────────────────────

test('declaredEnv() reads the gallery card shape used by CoworkingImageGallery.vue', () => {
  const card = { id: 'bu01', name: 'Browser Use', coworking_image: { env: BROWSER_USE_ENV } }
  assert.equal(declaredEnv(card).length, BROWSER_USE_ENV.length)
  assert.deepEqual(
    declaredEnv(card).map((field) => field.name),
    BROWSER_USE_ENV.map((field) => field.name)
  )
})

test('declaredEnv() reads a plain manifest, the shape the modal holds after selecting', () => {
  assert.deepEqual(declaredEnv({ env: BROWSER_USE_ENV }), BROWSER_USE_ENV)
})

test('declaredEnv() is defensive: no image, no env, wrong type or nameless entries', () => {
  assert.deepEqual(declaredEnv(null), [])
  assert.deepEqual(declaredEnv(undefined), [])
  assert.deepEqual(declaredEnv({}), [])
  assert.deepEqual(declaredEnv({ env: 'OPENAI_API_KEY' }), [])
  assert.deepEqual(declaredEnv({ env: [{ label: 'no name' }, { name: 'bad name' }, null] }), [])
})

test('declaredEnv() keeps an image that declares nothing out of the badge', () => {
  // The template hides the badge on `envFields(image).length`, so an empty
  // declaration must not render "0 env".
  assert.equal(declaredEnv({ coworking_image: { env: [] } }).length, 0)
})

// ── parseDotEnv ────────────────────────────────────────────────────────────

test('parseDotEnv() reads KEY=VALUE, export prefixes and comments', () => {
  const text = [
    '# browser-use',
    '',
    'OPENAI_API_KEY=placeholder-openai',
    'export BROWSER_USE_LLM_MODEL=gpt-4o',
    '   ANTHROPIC_API_KEY = placeholder-anthropic   '
  ].join('\n')
  assert.deepEqual(parseDotEnv(text), {
    OPENAI_API_KEY: 'placeholder-openai',
    BROWSER_USE_LLM_MODEL: 'gpt-4o',
    ANTHROPIC_API_KEY: 'placeholder-anthropic'
  })
})

test('parseDotEnv() strips one pair of quotes and keeps unknown keys for the mapper', () => {
  const text = ['A="two words"', "B='single'", 'C=a=b=c', 'D="half'].join('\r\n')
  assert.deepEqual(parseDotEnv(text), {
    A: 'two words',
    B: 'single',
    C: 'a=b=c',
    D: '"half'
  })
})

test('parseDotEnv() skips lines that are not assignments instead of aborting', () => {
  // An .env file is user input: one broken line must not lose the good ones.
  const text = ['not an assignment', '=orphan', '1INVALID=x', 'OPENAI_API_KEY=placeholder'].join('\n')
  assert.deepEqual(parseDotEnv(text), { OPENAI_API_KEY: 'placeholder' })
})

test('parseDotEnv() tolerates empty input', () => {
  assert.deepEqual(parseDotEnv(''), {})
  assert.deepEqual(parseDotEnv(null), {})
  assert.deepEqual(parseDotEnv(undefined), {})
})

test('unquote() only removes matching surrounding quotes', () => {
  assert.equal(unquote('"a"'), 'a')
  assert.equal(unquote("'a'"), 'a')
  assert.equal(unquote('"a\''), '"a\'')
  assert.equal(unquote('a'), 'a')
  assert.equal(unquote('"'), '"')
})

// ── presetDefaults / presetValues ──────────────────────────────────────────

test('presetDefaults() pre-fills the manifest defaults, blanks the rest', () => {
  const values = presetDefaults(BROWSER_USE_ENV)
  assert.equal(values.BROWSER_USE_LLM_MODEL, 'gpt-4o-mini')
  assert.equal(values.BROWSER_USE_HEADLESS, 'false')
  assert.equal(values.OPENAI_API_KEY, '')
  assert.deepEqual(Object.keys(values), BROWSER_USE_ENV.map((field) => field.name))
})

test('presetValues() sends only filled, trimmed values as extra_env', () => {
  // The dialog hands this object to the create-session payload unchanged.
  const values = presetValues(BROWSER_USE_ENV, {
    OPENAI_API_KEY: '  placeholder-openai  ',
    ANTHROPIC_API_KEY: '',
    BROWSER_USE_LLM_MODEL: 'gpt-4o-mini',
    BROWSER_USE_HEADLESS: '   ',
    BROWSER_USE_LOGGING_LEVEL: 'debug',
    // Not declared by the image: never leaks into extra_env.
    SOMETHING_ELSE: 'x'
  })
  assert.deepEqual(values, {
    OPENAI_API_KEY: 'placeholder-openai',
    BROWSER_USE_LLM_MODEL: 'gpt-4o-mini',
    BROWSER_USE_LOGGING_LEVEL: 'debug'
  })
})

test('presetValues() of an untouched form keeps the defaults and drops the secrets', () => {
  const values = presetValues(BROWSER_USE_ENV, presetDefaults(BROWSER_USE_ENV))
  assert.deepEqual(values, {
    BROWSER_USE_LLM_MODEL: 'gpt-4o-mini',
    BROWSER_USE_HEADLESS: 'false',
    BROWSER_USE_LOGGING_LEVEL: 'info'
  })
})

// ── mapDotEnv (the "Map .env" button) ──────────────────────────────────────

test('mapDotEnv() keeps declared keys and reports the ignored ones', () => {
  const dotEnv = parseDotEnv(
    [
      'OPENAI_API_KEY=placeholder-openai',
      'HTTPS_PROXY=http://proxy:3128',
      'BROWSER_USE_HEADLESS=true'
    ].join('\n')
  )
  const { values, mapped, ignored } = mapDotEnv(BROWSER_USE_ENV, dotEnv)
  assert.deepEqual(values, {
    OPENAI_API_KEY: 'placeholder-openai',
    BROWSER_USE_HEADLESS: 'true'
  })
  assert.deepEqual(mapped.sort(), ['BROWSER_USE_HEADLESS', 'OPENAI_API_KEY'])
  assert.deepEqual(ignored, ['HTTPS_PROXY'])
})

test('mapDotEnv() overlays the form: default < .env < typed', () => {
  // What resetEnvPreset + applyEnvFile + typing produce, step by step.
  const afterReset = { ...presetDefaults(BROWSER_USE_ENV) }
  const { values } = mapDotEnv(BROWSER_USE_ENV, parseDotEnv('BROWSER_USE_LLM_MODEL=claude-sonnet-4'))
  const afterImport = { ...afterReset, ...values }
  assert.equal(afterImport.BROWSER_USE_LLM_MODEL, 'claude-sonnet-4')
  assert.equal(afterImport.BROWSER_USE_HEADLESS, 'false', 'untouched default survives the import')

  const afterTyping = { ...afterImport, BROWSER_USE_HEADLESS: 'true' }
  assert.deepEqual(presetValues(BROWSER_USE_ENV, afterTyping), {
    BROWSER_USE_LLM_MODEL: 'claude-sonnet-4',
    BROWSER_USE_HEADLESS: 'true',
    BROWSER_USE_LOGGING_LEVEL: 'info'
  })
})

test('mapDotEnv() with an empty file changes nothing and ignores nothing', () => {
  assert.deepEqual(mapDotEnv(BROWSER_USE_ENV, {}), { values: {}, mapped: [], ignored: [] })
  assert.deepEqual(mapDotEnv([], { OPENAI_API_KEY: 'placeholder' }), {
    values: {},
    mapped: [],
    ignored: ['OPENAI_API_KEY']
  })
})

// ── missingRequired (creation guard) ───────────────────────────────────────

test('missingRequired() names the empty required fields by label', () => {
  const fields = [
    { name: 'PROXY_URL', label: 'Proxy URL', required: true },
    { name: 'OPENAI_API_KEY', label: 'OpenAI API key', required: false },
    { name: 'NO_LABEL', required: true }
  ]
  assert.deepEqual(missingRequired(fields, { PROXY_URL: 'http://p' }), ['NO_LABEL'])
  assert.deepEqual(missingRequired(fields, { NO_LABEL: 'x' }), ['Proxy URL'])
  assert.deepEqual(missingRequired(fields, {}), ['Proxy URL', 'NO_LABEL'])
})

test('missingRequired() treats a whitespace-only value as empty', () => {
  const fields = [{ name: 'PROXY_URL', label: 'Proxy URL', required: true }]
  assert.deepEqual(missingRequired(fields, { PROXY_URL: '   ' }), ['Proxy URL'])
})

test('missingRequired() of the browser-use preset is empty: every entry is optional', () => {
  assert.deepEqual(missingRequired(BROWSER_USE_ENV, presetValues(BROWSER_USE_ENV, {})), [])
})

Promise.all(pending).then(() => {
  process.exitCode = failed ? 1 : 0
})
