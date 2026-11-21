/**
 * ENV preset of a coworking image.
 *
 * A coworking image declares its environment in `<image>/image.json` under
 * `env`: one entry per variable with `name`, `label`, `description`,
 * `placeholder`, `default`, `choices`, `required` and `secret`. The gallery
 * shows how many entries an image carries and the create-session dialog turns
 * the declaration into the "Image settings" form whose values travel with the
 * session as `extra_env` (nothing is baked into the image).
 *
 * Precedence of a value, lowest first:
 *
 *   manifest `default`  <  mapped `.env` file  <  value typed in the form
 *
 * The functions here are pure so the whole rule set is testable without Vue
 * (`node --test tests/imageEnv.test.mjs`): the components only render and call
 * them.
 */

/** A variable name as accepted by Docker/`extra_env`. */
const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/

/**
 * ENV entries declared by an image manifest.
 *
 * Accepts both shapes the UI holds: a gallery card
 * (`{ coworking_image: { env } }`) and a plain manifest or library entry
 * (`{ env }`). Entries without a usable name are dropped, so a malformed
 * manifest never renders an empty row.
 *
 * @param {Object|null} image
 * @returns {Array<Object>} declared fields, in manifest order
 */
export function declaredEnv(image) {
  const fields = image?.coworking_image?.env ?? image?.env
  if (!Array.isArray(fields)) return []
  return fields.filter(
    (field) => field && typeof field.name === 'string' && NAME_PATTERN.test(field.name)
  )
}

/** Strip one pair of matching surrounding quotes. */
export function unquote(value) {
  const text = String(value ?? '').trim()
  if (text.length < 2) return text
  const first = text[0]
  if ((first === '"' || first === "'") && text[text.length - 1] === first) {
    return text.slice(1, -1)
  }
  return text
}

/**
 * Parse `.env` text into a plain object.
 *
 * Supported: `KEY=value`, `export KEY=value`, `#` comments, blank lines,
 * quoted values (`"a b"`, `'a b'`) and values containing `=`. A line without
 * `=` or with an invalid name is skipped: an `.env` file is user input, not a
 * form field, so a stray line must not abort the mapping.
 *
 * @param {string} text
 * @returns {Object<string, string>}
 */
export function parseDotEnv(text) {
  const values = {}
  String(text ?? '')
    .split('\n')
    .forEach((raw) => {
      const line = raw.trim().replace(/^export\s+/, '')
      if (!line || line.startsWith('#')) return
      const separator = line.indexOf('=')
      if (separator <= 0) return
      const name = line.slice(0, separator).trim()
      if (!NAME_PATTERN.test(name)) return
      values[name] = unquote(line.slice(separator + 1))
    })
  return values
}

/**
 * Values of an untouched form: the defaults declared by the manifest.
 *
 * @param {Array<Object>} fields
 * @returns {Object<string, string>}
 */
export function presetDefaults(fields) {
  const values = {}
  ;(fields || []).forEach((field) => {
    values[field.name] = field.default ?? ''
  })
  return values
}

/**
 * Map an `.env` object onto the declared fields.
 *
 * Only the keys the image declares are kept; the rest are reported as
 * `ignored` so the dialog can tell the user what it skipped.
 *
 * @param {Array<Object>} fields
 * @param {Object<string, string>} dotEnv
 * @returns {{values: Object<string, string>, mapped: string[], ignored: string[]}}
 */
export function mapDotEnv(fields, dotEnv) {
  const declared = new Set((fields || []).map((field) => field.name))
  const values = {}
  const ignored = []
  Object.entries(dotEnv || {}).forEach(([name, value]) => {
    if (!declared.has(name)) {
      ignored.push(name)
      return
    }
    values[name] = value
  })
  return { values, mapped: Object.keys(values), ignored }
}

/**
 * Non-empty (trimmed) values of the form: exactly what is sent as `extra_env`.
 *
 * @param {Array<Object>} fields
 * @param {Object<string, string>} typed
 * @returns {Object<string, string>}
 */
export function presetValues(fields, typed) {
  const values = {}
  ;(fields || []).forEach((field) => {
    const value = String(typed?.[field.name] ?? '').trim()
    if (value) values[field.name] = value
  })
  return values
}

/**
 * Required fields left empty, described by their label (the message the form
 * shows before creating the machine).
 *
 * @param {Array<Object>} fields
 * @param {Object<string, string>} values
 * @returns {string[]}
 */
export function missingRequired(fields, values) {
  return (fields || [])
    .filter((field) => field.required && !String(values?.[field.name] ?? '').trim())
    .map((field) => field.label || field.name)
}
