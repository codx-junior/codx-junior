// ─────────────────────────────────────────────────────────────────────────────
// Project links
//
// One project == one URL: "${origin}/${companySlug}/workspace/${project_name}".
// Switching project inside the app replaces the document with exactly that URL
// (see layout/AppsSection.vue), so opening a project in a NEW browser tab only
// needs to reuse it.
// ─────────────────────────────────────────────────────────────────────────────

import { $storex } from '@/store'

// Absolute URL of a project's workspace, or null when it can't be resolved.
// The company is the root: /<companySlug>/workspace/<projectName>.
export function projectUrl(project) {
  if (!project?.project_name) return null
  const companySlug = $storex.company?.activeCompany?.slug ||
    $storex.company?.defaultCompany?.slug
  if (!companySlug) return null
  return `${window.location.origin}/${encodeURIComponent(companySlug)}/workspace/${encodeURIComponent(project.project_name)}`
}

// Whether the event is the browser's "open in a new tab" gesture:
//   • ctrl+click (Linux / Windows) and cmd+click (macOS)
//   • middle click (reported through `auxclick`, never through `click`)
// Plain clicks never match, so callers keep their normal behaviour.
export function isOpenInNewTabEvent(event) {
  if (!event) return false
  if (event.type === 'auxclick') return event.button === 1
  return !!(event.ctrlKey || event.metaKey)
}

// Open the project in a brand new browser tab, leaving the current one untouched
export function openProjectInNewTab(project) {
  const url = projectUrl(project)
  if (!url) return null
  return window.open(url, '_blank', 'noopener')
}

export default { projectUrl, isOpenInNewTabEvent, openProjectInNewTab }