/**
 * Tests of the harness effort-tier helpers (client/src/utils/harnessMode.js).
 *
 * Run with: `cd client && node --test tests/harnessMode.test.mjs`
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  HARNESS_TIERS,
  followUpProfiles,
  modeForProfiles,
  tierHint,
  tierProfileName
} from '../src/utils/harnessMode.js'

test('maps the two tiers to their harness profiles', () => {
  assert.equal(tierProfileName('standard'), 'codx-junior')
  assert.equal(tierProfileName('deep'), 'codx-junior-max')
  // Unknown modes fall back to the standard tier.
  assert.equal(tierProfileName(''), 'codx-junior')
  assert.equal(HARNESS_TIERS.length, 2)
  assert.match(tierHint('deep'), /codx-junior-max/)
})

test('resolves the persisted tier from the session profiles', () => {
  assert.equal(modeForProfiles(['codx-junior', 'software_developer']), 'standard')
  assert.equal(modeForProfiles(['codx-junior-max', 'software_developer']), 'deep')
  assert.equal(modeForProfiles([]), 'standard')
  assert.equal(modeForProfiles(null), 'standard')
})

test('a follow-up replaces the effort tier and keeps the other profiles', () => {
  assert.deepEqual(
    followUpProfiles(['codx-junior', 'software_developer', 'qa'], 'deep'),
    ['codx-junior-max', 'software_developer', 'qa']
  )
  assert.deepEqual(
    followUpProfiles(['codx-junior-max', 'software_developer'], 'standard'),
    ['codx-junior', 'software_developer']
  )
  // max inherits codx-junior: never compose both tiers.
  assert.deepEqual(followUpProfiles(['codx-junior'], 'deep'), ['codx-junior-max'])
  // No duplicate profiles even when the caller already sent one.
  assert.deepEqual(
    followUpProfiles(['software_developer', 'software_developer'], 'standard'),
    ['codx-junior', 'software_developer']
  )
  assert.deepEqual(followUpProfiles([], 'standard'), ['codx-junior'])
})
