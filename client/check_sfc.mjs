/**
 * Throwaway CI helper: compile the actor SFCs with @vue/compiler-sfc.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

const root = dirname(new URL(import.meta.url).pathname)

const files = [
  'src/views/TeamView.vue',
  'src/components/actors/ActorsPanel.vue',
  'src/components/actors/ActorCard.vue',
  'src/components/actors/ActorEditorModal.vue',
  'src/components/actors/ScheduleEditor.vue'
]

let failed = 0
for (const file of files) {
  const source = readFileSync(join(root, file), 'utf8')
  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) {
    failed++
    console.log(`PARSE  ${file}: ${errors.map(e => e.message).join(' | ')}`)
    continue
  }
  try {
    if (descriptor.script || descriptor.scriptSetup) {
      compileScript(descriptor, { id: file })
    }
    const template = compileTemplate({
      source: descriptor.template.content,
      filename: file,
      id: file,
      compilerOptions: { expressionPlugins: [] }
    })
    if (template.errors.length) {
      failed++
      console.log(`TEMPLATE ${file}: ${template.errors.map(e => e.message || e).join(' | ')}`)
      continue
    }
    console.log(`ok     ${file}`)
  } catch (error) {
    failed++
    console.log(`SCRIPT ${file}: ${error.message}`)
  }
}
console.log(failed ? `FAILED ${failed}` : 'ALL OK')
