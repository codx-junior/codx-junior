// TEMPORARY probe (deleted right after use): asks the running vite dev server to
// transform every new calendar module. A 200 with a JS payload means the SFC
// compiles; a 500 means it does not.
const files = [
  '/src/views/CalendarView.vue',
  '/src/store/calendar.js',
  '/src/api/modules/calendar.js',
  '/src/components/calendar/MonthGrid.vue',
  '/src/components/calendar/WeekGrid.vue',
  '/src/components/calendar/AgendaList.vue',
  '/src/components/calendar/CalendarSidebar.vue',
  '/src/components/calendar/EventEditor.vue',
  '/src/components/calendar/EventPopover.vue',
  '/src/utils/calendarDate.js'
]

const base = process.argv[2] || 'http://127.0.0.1:5173'
let bad = 0

for (const file of files) {
  try {
    const res = await fetch(base + file)
    const body = await res.text()
    const broken = res.status !== 200 || body.startsWith('<!DOCTYPE')
    if (broken) bad++
    const preview = body.slice(0, 70).replace(/\s+/g, ' ')
    console.log(`${broken ? 'FAIL' : 'OK  '} ${res.status} ${String(body.length).padStart(6)} ${file}  ${preview}`)
  } catch (err) {
    bad++
    console.log(`ERR        ------ ${file}  ${err.message}`)
  }
}

console.log(bad === 0 ? 'ALL MODULES COMPILE' : `${bad} MODULE(S) FAILED`)
