import { createApp } from 'vue'
import './assets/main.css'
import GridPreview from './GridPreview.vue'

// Minimal `$storex` stand-in: the tile only needs the computer screenshot call
// to draw its previews. Inline images keep the layout check honest without
// touching the real API (and without network access).
const frame = (sessionId) => {
  const color = { s1: '#1d4ed8', s2: '#0f766e', s3: '#7c3aed', s4: '#6b7280', s5: '#b45309' }[sessionId] || '#334155'
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
    <rect width="1280" height="720" fill="${color}"/>
    <rect x="60" y="60" width="1160" height="80" rx="12" fill="#ffffff" opacity="0.85"/>
    <rect x="60" y="180" width="520" height="480" rx="12" fill="#ffffff" opacity="0.55"/>
    <rect x="620" y="180" width="600" height="220" rx="12" fill="#ffffff" opacity="0.35"/>
    <text x="100" y="118" font-family="monospace" font-size="46" fill="#111827">desktop ${sessionId}</text>
  </svg>`
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg)
}

const app = createApp(GridPreview)

app.config.globalProperties.$storex = {
  api: {
    computers: {
      screenshot: async (sessionId) => ({ success: true, screenshot: frame(sessionId) }),
      action: async () => ({ success: true, data: {} })
    }
  },
  browsers: {
    sessionById: () => null,
    activityFor: () => [],
    costFor: () => null
  },
  ui: { addNotification: () => {} },
  users: { user: { username: 'preview' } }
}

app.mount('#app')
