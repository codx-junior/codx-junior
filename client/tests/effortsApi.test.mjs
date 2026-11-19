/**
 * Contract tests for the client efforts API module.
 *
 * The module is the only place that builds the efforts URLs and picks the HTTP
 * verb, so the tests pin both against a fake API object: a wrong verb or a
 * wrong path here breaks Global Settings → Efforts silently, because the
 * backend router mirrors the same conventions.
 *
 * Dependency free on purpose (no test runner is installed), run with:
 *
 *   node tests/effortsApi.test.mjs
 */
import assert from 'node:assert/strict'
import { effortsModule } from '../src/api/modules/efforts.js'

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

/**
 * Fake API instance recording every call as `{ method, args }`.
 * @returns {Object} `{ calls, get, post, put, delete }`
 */
function fakeApi() {
  const calls = []
  const record = (method) => (...args) => {
    calls.push({ method, args })
    return Promise.resolve(args.length > 1 ? args[1] : args[0])
  }
  return {
    calls,
    get: record('get'),
    post: record('post'),
    put: record('put'),
    delete: record('delete')
  }
}

const lastCall = (API) => API.calls[API.calls.length - 1]

test('list() GETs /api/efforts', () => {
  const API = fakeApi()
  effortsModule(API).list()
  assert.deepEqual(lastCall(API), { method: 'get', args: ['/api/efforts'] })
})

test('fields() GETs /api/efforts/fields', () => {
  const API = fakeApi()
  effortsModule(API).fields()
  assert.deepEqual(lastCall(API), { method: 'get', args: ['/api/efforts/fields'] })
})

test('load() GETs /api/efforts/{name}', () => {
  const API = fakeApi()
  effortsModule(API).load('fast')
  assert.deepEqual(lastCall(API), { method: 'get', args: ['/api/efforts/fast'] })
})

test('load() trims the name and encodes reserved characters', () => {
  const API = fakeApi()
  effortsModule(API).load('  big model/v2  ')
  assert.deepEqual(lastCall(API), {
    method: 'get',
    args: ['/api/efforts/big%20model%2Fv2']
  })
})

test('create() POSTs /api/efforts with the payload untouched', () => {
  const API = fakeApi()
  const effort = { name: 'fast', description: 'quick', tier: 'agile', values: { max_rounds: 3 } }
  effortsModule(API).create(effort)
  assert.deepEqual(lastCall(API), { method: 'post', args: ['/api/efforts', effort] })
  assert.equal(lastCall(API).args[1], effort)
})

test('save() PUTs /api/efforts/{name} with the payload untouched', () => {
  const API = fakeApi()
  const effort = { description: 'quick', tier: 'agile', values: { max_rounds: 3 } }
  effortsModule(API).save('fast', effort)
  assert.deepEqual(lastCall(API), {
    method: 'put',
    args: ['/api/efforts/fast', effort]
  })
  assert.equal(lastCall(API).args[1], effort)
})

test('delete() DELETEs /api/efforts/{name}', () => {
  const API = fakeApi()
  effortsModule(API).delete('fast')
  assert.deepEqual(lastCall(API), { method: 'delete', args: ['/api/efforts/fast'] })
})

test('load/create/save/delete propagate the response', async () => {
  // The connection layer resolves the backend envelope (`{ status_code, message }`)
  // instead of throwing, so the module must not swallow it: the settings screen
  // inspects the value it gets back to surface a refused delete (409).
  const envelope = { status_code: 409, message: 'linked to profiles' }
  const respond = () => Promise.resolve(envelope)
  const efforts = effortsModule({
    get: respond,
    post: respond,
    put: respond,
    delete: respond
  })
  assert.equal(await efforts.load('fast'), envelope)
  assert.equal(await efforts.create({ name: 'fast' }), envelope)
  assert.equal(await efforts.save('fast', {}), envelope)
  assert.equal(await efforts.delete('fast'), envelope)
})

test('every instance talks only through its own API object', () => {
  const first = fakeApi()
  const second = fakeApi()
  effortsModule(first).list()
  assert.equal(first.calls.length, 1)
  assert.equal(second.calls.length, 0)
})

Promise.all(pending).then(() => {
  process.exitCode = failed ? 1 : 0
})
