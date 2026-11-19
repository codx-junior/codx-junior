/**
 * Shared presentation helpers of the CISO view.
 *
 * Kept in one place so the overview, findings, scans and activity panels use
 * the same colour for the same severity and the same label for the same scope.
 * These are presentation-only: no security decision is taken in the client.
 */

export const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']

export const SEVERITY_RANK = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  info: 0
}

export const CATEGORY_LABELS = {
  secret: 'Secrets',
  malicious: 'Malicious code',
  injection: 'Prompt injection',
  dangerous_operation: 'Dangerous operation',
  data_exfiltration: 'Data exfiltration',
  supply_chain: 'Supply chain',
  policy: 'Policy',
  configuration: 'Configuration'
}

export const ACTION_LABELS = {
  allow: 'Allowed',
  warn: 'Warning',
  require_approval: 'Needs approval',
  redact: 'Redacted',
  block: 'Blocked'
}

export const DOMAIN_LABELS = {
  file_read: 'File read',
  file_write: 'File write',
  git_commit: 'Commit',
  pull_request: 'Pull request',
  command: 'Command',
  container: 'Container',
  network: 'Network',
  model_output: 'Model output',
  knowledge_ingestion: 'Knowledge ingestion',
  profile: 'Profile',
  deploy: 'Deploy'
}

export const STATUS_LABELS = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled'
}

/** CSS class of a severity badge. */
export function severityClass(severity) {
  switch (String(severity || '').toLowerCase()) {
    case 'critical':
      return 'badge-error'
    case 'high':
      return 'badge-warning'
    case 'medium':
      return 'badge-info'
    case 'low':
      return 'badge-ghost'
    default:
      return 'badge-ghost'
  }
}

/** CSS class of a scan status badge. */
export function statusClass(status) {
  switch (String(status || '').toLowerCase()) {
    case 'completed':
      return 'badge-success'
    case 'running':
      return 'badge-info'
    case 'queued':
      return 'badge-ghost'
    case 'failed':
      return 'badge-error'
    case 'cancelled':
      return 'badge-warning'
    default:
      return 'badge-ghost'
  }
}

/** CSS class of a policy action badge. */
export function actionClass(action) {
  switch (String(action || '').toLowerCase()) {
    case 'block':
      return 'badge-error'
    case 'require_approval':
      return 'badge-warning'
    case 'redact':
      return 'badge-info'
    case 'warn':
      return 'badge-warning'
    default:
      return 'badge-success'
  }
}

/** Compact label of a severity (title case). */
export function severityLabel(severity) {
  const value = String(severity || '').toLowerCase()
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : '—'
}

/** Human label of a category. */
export function categoryLabel(category) {
  return CATEGORY_LABELS[category] || category || 'Other'
}

/** Human label of an action. */
export function actionLabel(action) {
  return ACTION_LABELS[action] || action || '—'
}

/** Human label of a domain. */
export function domainLabel(domain) {
  return DOMAIN_LABELS[domain] || domain || '—'
}

/** Human label of a scan scope. */
export function scopeLabel(scope) {
  switch (String(scope || '')) {
    case 'project':
      return 'Whole project'
    case 'changes':
      return 'Working tree + untracked'
    case 'staged':
      return 'Staged changes'
    case 'paths':
      return 'Selected paths'
    default:
      return scope || '—'
  }
}

/** Format an ISO timestamp for the UI (empty on invalid input). */
export function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

/** Relative age ("3m ago") used in compact headers. */
export function formatAge(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

/** Compact duration in seconds. */
export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '—'
  const value = Number(seconds)
  if (value < 1) return `${Math.round(value * 1000)} ms`
  if (value < 60) return `${value.toFixed(1)} s`
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`
}

/** Sort findings by severity, then path. */
export function sortFindings(findings) {
  return [...(findings || [])].sort((first, second) => {
    const rank =
      (SEVERITY_RANK[second?.severity] || 0) - (SEVERITY_RANK[first?.severity] || 0)
    if (rank !== 0) return rank
    return String(first?.path || '').localeCompare(String(second?.path || ''))
  })
}
