/**
 * Language catalogue shared by the account screen, the admin user maintenance
 * and voice conversation mode.
 *
 * The stored preference is a short language code (`en`, `es`, `pt`...) or
 * `auto`. Speech engines need a BCP-47 locale (`es-ES`, `pt-BR`...), so the
 * helpers below resolve one from the other and fall back to the browser
 * locale when the user has no explicit preference.
 */

export const USER_LANGUAGES = [
  { code: 'auto', label: 'Automatic (match my message)' },
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Español' },
  { code: 'pt', label: 'Português' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'it', label: 'Italiano' },
  { code: 'ca', label: 'Català' },
  { code: 'nl', label: 'Nederlands' },
  { code: 'ru', label: 'Русский' },
  { code: 'ar', label: 'العربية' },
  { code: 'zh', label: '中文' },
  { code: 'ja', label: '日本語' }
]

/** Default speech locale for each stored language code. */
export const SPEECH_LOCALES = {
  en: 'en-US',
  es: 'es-ES',
  pt: 'pt-BR',
  fr: 'fr-FR',
  de: 'de-DE',
  it: 'it-IT',
  ca: 'ca-ES',
  nl: 'nl-NL',
  ru: 'ru-RU',
  ar: 'ar-SA',
  zh: 'zh-CN',
  ja: 'ja-JP'
}

/** Older UI values that were stored before the shared catalogue existed. */
const LEGACY_LOCALES = {
  'es-SP': 'es-ES'
}

/** Locale options offered by the voice call setup. */
export const SPEECH_LANGUAGE_OPTIONS = USER_LANGUAGES
  .filter(language => language.code !== 'auto')
  .map(language => ({
    value: SPEECH_LOCALES[language.code],
    label: `${language.label} (${SPEECH_LOCALES[language.code]})`,
    code: language.code
  }))

/**
 * Human label for a stored language code or a resolved locale.
 * @param {string} value
 * @returns {string}
 */
export function languageLabel(value) {
  const text = String(value || '').trim()
  if (!text) return ''
  const direct = USER_LANGUAGES.find(language => language.code === text)
  if (direct) return direct.label
  const byLocale = USER_LANGUAGES.find(
    language => SPEECH_LOCALES[language.code] === text
  )
  if (byLocale) return `${byLocale.label} (${text})`
  const byBase = USER_LANGUAGES.find(
    language => language.code === text.split('-')[0].toLowerCase()
  )
  if (byBase) return `${byBase.label} (${text})`
  return text
}

/**
 * Normalise a browser language (`es_mx`, `EN-us`, ...) to a BCP-47 locale.
 * @param {string} value
 * @returns {string}
 */
export function normalizeBrowserLocale(value) {
  const text = String(value || '').trim().replace(/_/g, '-')
  if (!text) return ''
  const legacy = LEGACY_LOCALES[text] ||
    LEGACY_LOCALES[text.toLowerCase()] ||
    LEGACY_LOCALES[text.split('-')[0].toLowerCase()]
  if (legacy) return legacy
  const [base, ...rest] = text.split('-')
  if (!/^[a-z]{2,3}$/i.test(base)) return ''
  if (!rest.length) return SPEECH_LOCALES[base.toLowerCase()] || ''
  return [base.toLowerCase(), ...rest.map(part =>
    part.length === 2 ? part.toUpperCase() : part
  )].join('-')
}

/**
 * Resolve the language a voice call should speak/listen with.
 *
 * - explicit user language wins (`es`, `pt-BR`, ...);
 * - `auto`/missing uses the browser locale;
 * - a base-only preference keeps the browser region when it matches
 *   (`es` + `es-MX` -> `es-MX`);
 * - final fallback is `en-US`.
 *
 * @param {object} options
 * @param {string} [options.userLanguage]
 * @param {string} [options.browserLanguage]
 * @param {string} [options.fallback]
 * @returns {string}
 */
export function resolveSpeechLanguage({
  userLanguage = '',
  browserLanguage = '',
  fallback = 'en-US'
} = {}) {
  const preference = String(userLanguage || '').trim()
  const browser = normalizeBrowserLocale(browserLanguage)
  const preferred = preference.toLowerCase()

  if (preferred && preferred !== 'auto') {
    if (LEGACY_LOCALES[preference] || LEGACY_LOCALES[preferred]) {
      return LEGACY_LOCALES[preference] || LEGACY_LOCALES[preferred]
    }
    if (preference.includes('-')) {
      return normalizeBrowserLocale(preference) || preference
    }
    const base = preferred
    if (browser && browser.split('-')[0].toLowerCase() === base) {
      return browser
    }
    return SPEECH_LOCALES[base] || normalizeBrowserLocale(fallback) || 'en-US'
  }

  return browser || normalizeBrowserLocale(fallback) || 'en-US'
}

export default {
  SPEECH_LANGUAGE_OPTIONS,
  SPEECH_LOCALES,
  USER_LANGUAGES,
  languageLabel,
  normalizeBrowserLocale,
  resolveSpeechLanguage
}
