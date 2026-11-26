import { defineConfig } from 'vitepress'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: 'codx-junior',
  description: 'The open-source workspace transforming how software projects are built.',
  lang: 'en-US',
  base: '/',
  // Internal engineering reviews live next to the feature pages they audit. They
  // are not in the sidebar, but the build would publish them and the local search
  // index (search.provider 'local') would surface them. Keep them off the site.
  // Transformation material is internal until management decides to publish (IT team card 721dc223).
  srcExclude: ['features/workspaces-*-review.md', 'transformation/**', 'product/**'],
  // Many cross-links are added as features grow; don't fail the build on them.
  ignoreDeadLinks: true,
  head: [
    ['link', { rel: 'icon', href: '/only_icon.png' }],
    ['meta', { name: 'theme-color', content: '#550e57' }]
  ],
  themeConfig: {
    logo: '/only_icon.png',
    siteTitle: 'codx-junior',
    // https://vitepress.dev/reference/default-theme-config
    nav: [
      { text: '0.0.1', link: '/0.0.1' },
      { text: 'Advanced (dev)', link: '/advanced' },
      { text: 'Guide', link: '/guide/introduction' },
      { text: 'Features', link: '/features/' },
      { text: 'Getting started', link: '/getting-started' },
      { text: 'Docker Hub', link: '/docker-hub' },
      { text: 'Configuration', link: '/configuration' },
      { text: 'Demo', link: '/demo' },
      { text: 'Team', link: '/team' }
    ],
    sidebar: {
      '/guide/': [
        {
          text: 'Guide',
          items: [
            { text: 'What is codx-junior?', link: '/guide/introduction' },
            { text: 'Architecture', link: '/guide/architecture' }
          ]
        }
      ],
      '/features/': [
        {
          text: 'Available in 0.0.1',
          items: [
            { text: 'Overview', link: '/features/' },
            { text: 'Chats and agents', link: '/features/chats-and-agents' },
            { text: 'Agent team & profiles', link: '/features/profiles' },
            { text: 'Knowledge (RAG)', link: '/features/knowledge' },
            { text: 'Vibe coding', link: '/features/vibe-coding' },
            { text: 'Kanban and tasks', link: '/features/kanban' },
            { text: 'Git and code review', link: '/features/code-review' },
            { text: 'Quality guardians', link: '/features/quality-guardians' },
            { text: 'AI providers and models', link: '/features/ai-providers' },
            { text: 'Development tools (file explorer)', link: '/features/development-tools' },
            { text: 'API', link: '/features/api' }
          ]
        },
        {
          text: 'Roadmap (hidden in 0.0.1)',
          items: [
            { text: 'Suggested replies', link: '/features/suggested-replies' },
            { text: 'Voice conversation', link: '/features/voice-conversation' },
            { text: 'Autonomous agents', link: '/features/autonomous-agents' },
            { text: 'Autonomous tickets', link: '/features/autonomous-tickets' },
            { text: 'Company assistant', link: '/features/company-assistant' },
            { text: 'Company tickets', link: '/features/company-tickets' },
            { text: 'Company files and coworking spaces', link: '/features/company-spaces' },
            { text: 'Teams and messenger', link: '/features/teams' },
            { text: 'Company meetings', link: '/features/company-meetings' },
            { text: 'Channel content creator', link: '/features/channel-creator' },
            { text: 'Slack and WhatsApp channels', link: '/features/slack-whatsapp-integration' },
            { text: 'External actors and outreach', link: '/features/external-actors' },
            { text: 'Workspaces', link: '/features/workspaces' },
            { text: 'Computer use', link: '/features/computer-use' },
            { text: 'Browser use and co-browsing', link: '/features/browser-use' },
            { text: '@codx mentions', link: '/features/mentions' },
            { text: 'Wiki', link: '/features/wiki' },
            { text: 'CISO', link: '/features/ciso' },
            { text: 'Budget and analytics', link: '/features/budget-and-analytics' },
            { text: 'Users and security', link: '/features/users-and-security' },
            { text: 'MCP connections', link: '/features/mcp-connections' },
            { text: 'Plugins', link: '/features/plugins' },
            { text: 'DSH task sync', link: '/features/dsh-sync' },
            { text: 'codx-junior harness', link: '/features/codx-junior-harness' }
          ]
        }
      ],
      '/': [
        {
          text: '0.0.1 release',
          items: [
            { text: 'What ships in 0.0.1', link: '/0.0.1' },
            { text: 'Advanced platform (in development)', link: '/advanced' },
            { text: 'Feature matrix (GitHub)', link: 'https://github.com/codx-junior/codx-junior/blob/main/docs/product/v0.0.1-feature-matrix.md' }
          ]
        },
        {
          text: 'Introduction',
          items: [
            { text: 'What is codx-junior?', link: '/guide/introduction' },
            { text: 'Architecture', link: '/guide/architecture' }
          ]
        },
        {
          text: 'Getting started',
          items: [
            { text: 'Self-host with Docker', link: '/getting-started' },
            { text: 'Docker Hub image', link: '/docker-hub' },
            { text: 'Initial setup', link: '/initial-setup' },
            { text: 'Configuration', link: '/configuration' },
            { text: 'Self-hosted or managed', link: '/managed' }
          ]
        },
        {
          text: 'Features',
          items: [{ text: 'Feature overview', link: '/features/' }]
        },
        {
          text: 'Community',
          items: [
            { text: 'Demo', link: '/demo' },
            { text: 'Team', link: '/team' }
          ]
        }
      ]
    },
    search: {
      provider: 'local'
    },
    outline: {
      level: [2, 3],
      label: 'On this page'
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/gbrian/codx-junior' }
    ],
    editLink: {
      pattern: 'https://github.com/gbrian/codx-junior/edit/main/docs/:path',
      text: 'Edit this page on GitHub'
    },
    footer: {
      message: 'Released under the AGPL-3.0 License.',
      copyright: 'Copyright © codx-junior — You will never code alone! ❤️'
    }
  }
})
