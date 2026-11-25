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
  srcExclude: ['features/workspaces-*-review.md', 'transformation/**'],
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
      { text: 'Guide', link: '/guide/introduction' },
      { text: 'Features', link: '/features/' },
      { text: 'Getting started', link: '/getting-started' },
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
          text: 'Features',
          items: [
            { text: 'Overview', link: '/features/' },
            { text: 'Chats and agents', link: '/features/chats-and-agents' },
            { text: 'Suggested replies', link: '/features/suggested-replies' },
            { text: 'Voice conversation', link: '/features/voice-conversation' },
            { text: 'Kanban and tasks', link: '/features/kanban' },
            { text: 'Autonomous tickets', link: '/features/autonomous-tickets' },
            { text: 'codx-junior harness (@codx-junior)', link: '/features/codx-junior-harness' },
            { text: 'DSH task sync', link: '/features/dsh-sync' },
            { text: 'Vibe coding', link: '/features/vibe-coding' },
            { text: 'Workspaces', link: '/features/workspaces' },
            { text: 'Computer use (desktops, VMs, remote)', link: '/features/computer-use' },
            { text: 'Company assistant (real chat)', link: '/features/company-assistant' },
            { text: 'Company tickets (unattended workbench)', link: '/features/company-tickets' },
            { text: 'Company files and coworking spaces', link: '/features/company-spaces' },
            { text: 'Browser use and co-browsing', link: '/features/browser-use' },
            { text: 'Teams and messenger', link: '/features/teams' },
            { text: 'Channel content creator', link: '/features/channel-creator' },
            { text: 'Company meetings', link: '/features/company-meetings' },
            { text: 'Slack and WhatsApp channels', link: '/features/slack-whatsapp-integration' },
            { text: 'External actors and outreach', link: '/features/external-actors' },
            { text: 'Knowledge (RAG)', link: '/features/knowledge' },
            { text: 'Profiles', link: '/features/profiles' },
            { text: '@codx mentions', link: '/features/mentions' },
            { text: 'Wiki', link: '/features/wiki' },
            { text: 'CISO (security view)', link: '/features/ciso' },
            { text: 'Git and code review', link: '/features/code-review' },
            { text: 'AI providers and models', link: '/features/ai-providers' },
            { text: 'MCP connections', link: '/features/mcp-connections' },
            { text: 'Budget and analytics', link: '/features/budget-and-analytics' },
            { text: 'Users and security', link: '/features/users-and-security' },
            { text: 'Development tools', link: '/features/development-tools' },
            { text: 'Plugins', link: '/features/plugins' },
            { text: 'API', link: '/features/api' }
          ]
        }
      ],
      '/': [
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
