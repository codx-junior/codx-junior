import { actions } from './store/browsers'

const results = []

function run(label, state, payload) {
  const commits = []
  const ctx = {
    state: {
      sessions: [
        { id: 'run1', status: 'running' },
        { id: 'run2', status: 'running' },
        { id: 'stop1', status: 'stopped' }
      ],
      selectedSessionId: null,
      gridSessionIds: [],
      gridActiveId: null,
      ...state
    },
    commit(type, value) {
      commits.push([type, value])
    }
  }
  actions.setViewMode(ctx, payload)
  results.push({ label, commits })
}

run('grid seeds watched + running', { selectedSessionId: 'run2' }, 'grid')
run('grid seeds running only', {}, 'grid')
run('grid keeps an existing wall', { gridSessionIds: ['keep'] }, 'grid')
run('restore does not seed', {}, { mode: 'grid', seed: false })
run('single never seeds', {}, 'single')
run('unknown mode falls back to single', {}, 'whatever')

window.__storeCheck = results
