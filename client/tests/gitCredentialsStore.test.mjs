/**
 * Store-token flow of the Git view credential dialog.
 *
 * A push to a remote without credentials opens the dialog. The user can tick
 * "store this token" so the accepted token is written to the project settings
 * (`git_access_token`) and later fetch/pull/push calls do not ask again.
 *
 * These tests pin the two promises that make that safe:
 *
 *  - only an access token can be stored (a username/password pair stays
 *    one-shot),
 *  - the token is saved only after the retry succeeded and only when the box
 *    was ticked, so a rejected value never replaces a working setting.
 *
 * The component sources are read as text and never evaluated, so no test
 * builds a function from a string.
 *
 * Run with: node --test tests/*.test.mjs
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const dialogSource = await read('../src/components/repo/GitCredentialsDialog.vue')
const gitViewSource = await read('../src/components/repo/GitView.vue')
const settingsSource = await read('../src/views/ProjectSettings.vue')

/** Plain `<script>` block of the dialog (it declares no imports). */
const dialogScript = dialogSource.match(/<script>([\s\S]*?)<\/script>/)[1]

/** Body of one dialog option, so a renamed or missing option fails loudly. */
const dialogOption = (pattern, label) => {
  const match = dialogScript.match(pattern)
  if (!match) throw new Error(`GitCredentialsDialog.vue no longer declares ${label}`)
  return match[1]
}

const canStoreTokenBody = dialogOption(/\n {4}canStoreToken\(\) \{([\s\S]*?)\n {4}\}/, 'canStoreToken()')
const submitBody = dialogOption(/\n {4}submit\(\) \{([\s\S]*?)\n {4}\}/, 'submit()')
const resetBody = dialogOption(/\n {4}reset\(\) \{([\s\S]*?)\n {4}\}/, 'reset()')
const tokenWatchBody = dialogOption(/\n {4}token\(value\) \{([\s\S]*?)\n {4}\}/, 'the token watcher')

test('only an access token can be stored in the project settings', () => {
  assert.match(
    canStoreTokenBody,
    /return !this\.busy && !!this\.token\.trim\(\)/,
    'storing follows the token only, and a blank token is never storable'
  )
  assert.doesNotMatch(canStoreTokenBody, /username|password/, 'a password cannot be stored')
})

test('submitting emits the credential and wipes the component copy', () => {
  assert.match(submitBody, /if \(!this\.canSubmit\) return/, 'an empty form is not submitted')
  assert.match(submitBody, /username: this\.username\.trim\(\)/)
  assert.match(submitBody, /password: this\.password/)
  assert.match(submitBody, /token: this\.token\.trim\(\)/)
  assert.match(
    submitBody,
    /storeToken: this\.storeToken && this\.canStoreToken/,
    'the store flag is revalidated when the credential is emitted'
  )
  assert.match(
    resetBody,
    /this\.username = ''[\s\S]*this\.password = ''[\s\S]*this\.token = ''[\s\S]*this\.storeToken = false/,
    'reset drops every value the component holds'
  )
  assert.ok(
    submitBody.indexOf('this.reset()') < submitBody.indexOf("this.$emit('submit'"),
    'the copy is wiped before the credential leaves the component'
  )
})

test('a username/password pair is never requested for storage', () => {
  assert.doesNotMatch(dialogScript, /storeToken: true/, 'the flag is only ever set from the checkbox')
  assert.match(
    dialogScript,
    /storeToken: this\.storeToken && this\.canStoreToken/,
    'only an accepted token can carry the store flag'
  )
  assert.doesNotMatch(canStoreTokenBody, /username|password/, 'the pair has no setting to be stored in')
})

test('clearing the token clears the store checkbox', () => {
  assert.match(
    tokenWatchBody,
    /if \(!String\(value \|\| ''\)\.trim\(\)\) this\.storeToken = false/,
    'emptying the token unticks the box'
  )
})

test('the Git view saves the token only after the retry succeeded', () => {
  const submitSource = gitViewSource.slice(
    gitViewSource.indexOf('async submitCredentials('),
    gitViewSource.indexOf('async storeGitToken(')
  )

  assert.match(submitSource, /const storeToken = !!values\.storeToken/)
  assert.match(submitSource, /await this\.runOperation\(/)
  assert.match(
    submitSource,
    /if \(storeToken && !this\.error\) \{\s*await this\.storeGitToken\(values\.token, request\.operation\)/,
    'a failed retry must not overwrite the stored token'
  )
  assert.ok(
    submitSource.indexOf('await this.runOperation(') <
      submitSource.indexOf('await this.storeGitToken('),
    'the token is stored after the remote accepted it'
  )
})

test('the Git view writes the accepted token to the project settings', () => {
  const storeSource = gitViewSource.slice(
    gitViewSource.indexOf('async storeGitToken('),
    gitViewSource.indexOf('closeCredentials()')
  )

  assert.match(storeSource, /const value = String\(token \|\| ''\)\.trim\(\)/)
  assert.match(storeSource, /const settings = await this\.api\.settings\.read\(\)/)
  assert.match(
    storeSource,
    /await this\.api\.settings\.save\(\{ \.\.\.settings, git_access_token: value \}\)/,
    'the project setting used by the backend is written'
  )
  assert.match(storeSource, /this\.project\.git_access_token = value/)
})

test('the settings page edits the project token, not the global copy', () => {
  const bindings = settingsSource.match(/v-model="settings\.git_access_token"/g) || []

  assert.equal(bindings.length, 2, 'both the mobile and desktop layouts expose it')
  assert.doesNotMatch(
    settingsSource,
    /v-model="globalGit\.git_access_token"/,
    'the token used by the git view lives in the project settings'
  )
})
