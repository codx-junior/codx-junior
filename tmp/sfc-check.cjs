// Temporary SFC syntax check (safe to delete).
const fs = require('fs')
const p = require('/home/codx-junior/codx-junior/client/node_modules/@vue/compiler-sfc')
const babel = require('/home/codx-junior/codx-junior/client/node_modules/@babel/parser')

const files = process.argv.slice(2)
let failed = false

for (const f of files) {
  try {
    const src = fs.readFileSync(f, 'utf8')
    const { descriptor, errors } = p.parse(src)
    if (errors.length) throw new Error(JSON.stringify(errors, null, 1))
    const t = p.compileTemplate({
      source: descriptor.template.content,
      filename: f,
      id: 'check'
    })
    if (t.errors.length) throw new Error(JSON.stringify(t.errors, null, 1))
    const script = (descriptor.script && descriptor.script.content) ||
      (descriptor.scriptSetup && descriptor.scriptSetup.content) || ''
    babel.parse(script, { sourceType: 'module' })
    console.log('OK   ' + f)
  } catch (e) {
    failed = true
    console.log('FAIL ' + f + '\n' + (e.message || e))
  }
}

process.exit(failed ? 1 : 0)
