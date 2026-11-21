/**
 * Compile-check SFCs with the project's own Vue compiler.
 * Usage: node tmp-vue-sfc-check.mjs <file.vue> [...]
 */
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'

let failed = false
for (const file of process.argv.slice(2)) {
  const source = readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) {
    failed = true
    console.error('PARSE', file, errors.map(error => String(error)))
    continue
  }
  try {
    compileScript(descriptor, { id: 'check' })
    const result = compileTemplate({
      source: descriptor.template.content,
      filename: file,
      id: 'check'
    })
    if (result.errors.length) {
      failed = true
      console.error('TEMPLATE', file, result.errors.map(error => String(error)))
      continue
    }
    console.log('OK', file)
  } catch (error) {
    failed = true
    console.error('SCRIPT', file, error.message)
  }
}
process.exit(failed ? 1 : 0)
