import config from './config.json' with {type: 'json'};
import { defineConfig } from 'vitepress'
import { curateSidebar } from '../scripts/sidebar-curation.mjs'

const {
  project: {
    name: title,
    description,
    icon,
    repository
  },
  sidebar,
  base
} = config as any

const socialLinks = []
if (repository) {
  socialLinks.push({ icon: 'github', link: repository })
}

// Public 0.0.1 entry points; the generated navigation is curated so internal
// engineering pages never appear in the public site.
const publicSidebar = [
  {
    text: 'codx-junior 0.0.1',
    items: [
      { text: 'Why codx-junior', link: '/home' },
      { text: 'What ships in 0.0.1', link: '/0.0.1' },
      { text: 'Install', link: '/install' }
    ]
  }
]

const curatedSidebar = [...publicSidebar, ...curateSidebar(sidebar)]

// The generated wiki is deployable as its own website. `base` is written by
// the site builder (`/` for standalone deployments, a sub-path when hosted
// under codx-junior).
export default defineConfig({
  title,
  description,
  base: base || '/',
  ignoreDeadLinks: true,
  head: [
    ['meta', { name: 'theme-color', content: '#550e57' }]
  ],
  themeConfig: {
    search: { provider: 'local' },
    logo: icon,
    nav: [
      { text: 'Home', link: '/' },
      { text: '0.0.1', link: '/0.0.1' },
      { text: 'Install', link: '/install' },
      { text: 'GitHub', link: 'https://github.com/codx-junior/codx-junior' }
    ],
    sidebar: curatedSidebar,
    socialLinks
  }
})
