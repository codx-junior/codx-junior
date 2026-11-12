// TEMPORARY verification helper — deleted right after use (M0 calendar step).
// Runs the project's own type-check (vue-tsc --build --force) and summarises
// which files report errors, so a step can prove whether a failure pre-exists.
import { spawnSync } from 'node:child_process'

const CLIENT = '/home/codx-junior/codx-junior/client'
const VUE_TSC = `${CLIENT}/node_modules/vue-tsc/bin/vue-tsc.js`

const result = spawnSync(process.execPath, [VUE_TSC, '--build', `${CLIENT}/tsconfig.json`, '--force'], {
  cwd: CLIENT,
  encoding: 'utf8',
  maxBuffer: 1024 * 1024 * 32
})

const output = `${result.stdout || ''}${result.stderr || ''}`
const errors = output.split('\n').filter((line) => line.includes('error TS'))
const files = [...new Set(errors.map((line) => line.split('(')[0].trim()))].sort()
const calendarErrors = errors.filter((line) => /calendar/i.test(line))

console.log('exit code:', result.status)
console.log('total error lines:', errors.length)
console.log('--- files with errors ---')
console.log(files.join('\n'))
console.log('--- calendar-related errors ---')
console.log(calendarErrors.length ? calendarErrors.join('\n') : '(none)')
