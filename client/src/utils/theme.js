/**
 * Product theme names.
 *
 * The company workspace and the project workspace share one daisyUI theme.
 * `light` and `dark` were the stock defaults before the codx-junior themes
 * existed; persisted UI state and user profiles can still contain them, so
 * every entry point normalises them to the branded pair.
 */
export const DEFAULT_THEME = 'codx-junior-dark'
export const BRAND_THEMES = {
  dark: 'codx-junior-dark',
  light: 'codx-junior-light'
}

export function normalizeTheme(theme) {
  const value = String(theme || '').trim()
  if (BRAND_THEMES[value]) return BRAND_THEMES[value]
  return value || DEFAULT_THEME
}
