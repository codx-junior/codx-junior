import test from 'node:test'
import assert from 'node:assert/strict'
import { readdir, readFile, stat } from 'node:fs/promises'
import { DEFAULT_THEME, normalizeTheme } from '../src/utils/theme.js'

/**
 * Company theme contract.
 *
 * The company workspace used to ship its own stylesheet (assets/company.css)
 * with a private `.company-shell` palette and `cp-*` component classes. That
 * made the company view drift from the project workspace. Company styling now
 * comes from the same daisyUI/Tailwind themes as the rest of the app; these
 * assertions keep it that way.
 */

const read = path => readFile(new URL(path, import.meta.url), 'utf8')
const companyDir = new URL('../src/components/company/', import.meta.url)

const companyView = await read('../src/views/CompanyView.vue')
const baseCss = await read('../src/assets/base.css')
const uiStore = await read('../src/store/ui.js')
const themeSelector = await read('../src/components/ThemeSelector.vue')
const themeDark = await read('../src/assets/themes/codx-junior-dark.css')
const themeLight = await read('../src/assets/themes/codx-junior-light.css')
const companyComponents = await Promise.all(
  (await readdir(companyDir))
    .filter(name => name.endsWith('.vue'))
    .map(name => readFile(new URL(name, companyDir), 'utf8'))
)

test('the company workspace has no private stylesheet', async () => {
  await assert.rejects(stat(new URL('../src/assets/company.css', import.meta.url)))
  assert.doesNotMatch(companyView, /assets\/company\.css/)
  assert.doesNotMatch(companyView, /data-company-theme/)
  assert.doesNotMatch(companyView, /\bcp-[a-z]/)
})

test('company components use the shared Tailwind/daisyUI theme', () => {
  for (const source of companyComponents) {
    assert.doesNotMatch(source, /\bvar\(--cp-/, 'no private company CSS variables')
    assert.doesNotMatch(source, /\bcp-[a-z]/, 'no private company component classes')
  }
})

test('the company shell uses semantic theme utilities', () => {
  assert.match(companyView, /class="[^"]*\bbg-base-100\b/)
  assert.match(companyView, /class="[^"]*\btext-base-content\b/)
  assert.match(companyView, /\$storex\.ui\.theme/, 'the quick theme toggle reads the app theme')
  assert.match(companyView, /\$storex\.ui\.setTheme/, 'the quick theme toggle writes the app theme')
})

test('the brand logo colours are the Tailwind primary and secondary', () => {
  // The mark uses #CD0099 (medium violet red) and #8F008E (dark magenta).
  assert.match(baseCss, /--color-brand:\s*#CD0099/)
  assert.match(baseCss, /--color-brand-dark:\s*#8F008E/)
  for (const css of [themeDark, themeLight]) {
    assert.match(css, /--color-primary:\s*var\(--color-brand\)/)
    assert.match(css, /--color-secondary:\s*var\(--color-brand-dark\)/)
    assert.match(css, /--color-accent:\s*var\(--color-brand\)/)
    assert.match(css, /--color-primary-content:\s*#ffffff/)
    assert.match(css, /--color-secondary-content:\s*#ffffff/)
    assert.match(css, /--color-accent-content:\s*#ffffff/)
  }
  assert.match(themeLight, /--color-success-ink:\s*#1f7a34/)
  assert.match(themeDark, /--color-success-ink:\s*#6fd89a/)
})

test('legacy theme names resolve to the codx-junior palette', () => {
  assert.equal(DEFAULT_THEME, 'codx-junior-dark')
  assert.equal(normalizeTheme('dark'), 'codx-junior-dark')
  assert.equal(normalizeTheme('light'), 'codx-junior-light')
  assert.equal(normalizeTheme('cupcake'), 'cupcake')
  assert.equal(normalizeTheme(''), 'codx-junior-dark')
  assert.match(uiStore, /normalizeTheme\(state\.theme\)/)
  assert.match(uiStore, /normalizeTheme\(API\.user\.theme\)/)
  assert.doesNotMatch(themeSelector, /data-set-theme="dark"/)
  assert.doesNotMatch(themeSelector, /data-set-theme="light"/)
})

test('semantic text tones come from theme ink tokens', () => {
  for (const tone of ['primary', 'secondary', 'accent', 'info', 'success', 'warning', 'error']) {
    assert.match(baseCss, new RegExp(`--color-${tone}-ink`))
  }
  assert.match(companyView, /text-accent-ink|text-primary-ink/)
})

test('the codx-junior daisyUI themes are registered', () => {
  assert.match(baseCss, /@import "\.\/themes\/codx-junior-light\.css"/)
  assert.match(baseCss, /@import "\.\/themes\/codx-junior-dark\.css"/)
  assert.match(themeSelector, /codx-junior-dark/)
  assert.match(themeSelector, /codx-junior-light/)
  assert.match(uiStore, /theme: DEFAULT_THEME/)
})
