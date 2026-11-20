import { defineConfig } from 'vitest/config'

/**
 * The docs site is verified as data, not rendered: the tests import the real
 * `.vitepress/config.mts` and assert on that object plus the markdown files on
 * disk. No browser, no jsdom, no coverage threshold — a coverage percentage over
 * static markdown would be a number without a meaning.
 *
 * `.mts` is mandatory: `package.json` is `"type": "commonjs"`, so a `.ts` file
 * here would be loaded as CommonJS and fail on the first `import`.
 */
export default defineConfig({
  test: {
    include: ['qa/tests/**/*.test.mts'],
    environment: 'node',
    // A run that discovers no test must fail, not report success. This is the
    // `--passWithNoTests` trap that made the QA gate green without running
    // anything (card 6c697929).
    passWithNoTests: false,
  },
})
