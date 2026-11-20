<script setup>
/**
 * CISO rules & policy: what is detected and what the platform does with it.
 *
 * Two halves, one source of truth:
 *   - the detector catalogue (100 deterministic rules) can be silenced one by
 *     one; the toggle writes the project `ciso_policy.ignore_rules`, and the
 *     next interactive gate honours it. The pre-commit protection floor always
 *     enforces the core families, even when a rule is silenced here;
 *   - the decision ladder (block / approval / warn) and the scanner options are
 *     edited as a validated partial policy. The backend rejects unknown fields
 *     and invalid enum values instead of silently ignoring them.
 */
import { computed, onMounted, ref } from 'vue'
import {
  categoryLabel,
  severityClass,
  severityLabel
} from './format'

const props = defineProps({
  api: { type: Object, default: null }
})

const emit = defineEmits(['changed'])

const policyState = ref(null)
const rules = ref([])
const counts = ref({})
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const ruleBusy = ref('')
const form = ref(null)

const ruleSearch = ref('')
const ruleSource = ref('')
const ruleScope = ref('')
const showOnlyEnabled = ref(false)

const sources = computed(() => {
  const seen = new Map()
  rules.value.forEach(rule => seen.set(rule.source, rule.source_label || rule.source))
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
})

const filteredRules = computed(() => {
  const needle = ruleSearch.value.trim().toLowerCase()
  return rules.value.filter(rule => {
    if (showOnlyEnabled.value && !rule.enabled) return false
    if (ruleSource.value && rule.source !== ruleSource.value) return false
    if (ruleScope.value && rule.scope !== ruleScope.value) return false
    if (!needle) return true
    return (
      rule.rule_id.toLowerCase().includes(needle) ||
      String(rule.title || '').toLowerCase().includes(needle) ||
      String(rule.remediation || '').toLowerCase().includes(needle)
    )
  })
})

const categories = computed(() => policyState.value?.options?.categories || [])
const domains = computed(() => policyState.value?.options?.domains || [])
const severities = computed(() => policyState.value?.options?.severities || [])

const dirty = computed(() => {
  if (!policyState.value || !form.value) return false
  const original = editableProjection(policyState.value.policy)
  const current = editableProjection(form.value)
  return JSON.stringify(original) !== JSON.stringify(current)
})

function editableProjection(source) {
  if (!source) return null
  return {
    enabled: !!source.enabled,
    block_above: source.block_above,
    approval_above: source.approval_above,
    warn_above: source.warn_above,
    redact_secrets: !!source.redact_secrets,
    fail_closed: !!source.fail_closed,
    block_severities: [...(source.block_severities || [])].sort(),
    block_categories: [...(source.block_categories || [])].sort(),
    strict_domains: [...(source.strict_domains || [])].sort(),
    enabled_domains: [...(source.enabled_domains || [])].sort(),
    ignore_paths: [...(source.ignore_paths || [])].sort(),
    allow_secret_files: [...(source.allow_secret_files || [])].sort(),
    entropy_threshold: Number(source.entropy_threshold),
    min_secret_length: Number(source.min_secret_length),
    max_scan_bytes: Number(source.max_scan_bytes),
    max_excerpt_chars: Number(source.max_excerpt_chars),
    secrets: {
      enabled: !!source.secrets?.enabled,
      require_grant: !!source.secrets?.require_grant,
      allow_env_source: !!source.secrets?.allow_env_source
    }
  }
}

function resetForm() {
  const policy = policyState.value?.policy || {}
  form.value = {
    enabled: !!policy.enabled,
    block_above: policy.block_above || 'high',
    approval_above: policy.approval_above || 'medium',
    warn_above: policy.warn_above || 'low',
    redact_secrets: !!policy.redact_secrets,
    fail_closed: !!policy.fail_closed,
    block_severities: [...(policy.block_severities || [])],
    block_categories: [...(policy.block_categories || [])],
    strict_domains: [...(policy.strict_domains || [])],
    enabled_domains: [...(policy.enabled_domains || [])],
    ignore_paths: [...(policy.ignore_paths || [])],
    allow_secret_files: [...(policy.allow_secret_files || [])],
    entropy_threshold: Number(policy.entropy_threshold ?? 4.2),
    min_secret_length: Number(policy.min_secret_length ?? 20),
    max_scan_bytes: Number(policy.max_scan_bytes ?? 1048576),
    max_excerpt_chars: Number(policy.max_excerpt_chars ?? 160),
    secrets: {
      enabled: !!policy.secrets?.enabled,
      require_grant: !!policy.secrets?.require_grant,
      allow_env_source: !!policy.secrets?.allow_env_source
    }
  }
}

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

async function load() {
  if (!props.api) return
  loading.value = true
  error.value = ''
  try {
    const [catalogue, policy] = await Promise.all([
      props.api.ciso.rules(),
      props.api.ciso.policy()
    ])
    rules.value = catalogue?.rules || []
    counts.value = catalogue?.counts || {}
    policyState.value = policy
    resetForm()
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    loading.value = false
  }
}

function inList(list, value) {
  return Array.isArray(list) && list.includes(value)
}

function toggleList(list, value) {
  const index = list.indexOf(value)
  if (index >= 0) list.splice(index, 1)
  else list.push(value)
}

function save() {
  if (!props.api || !form.value || saving.value) return
  // Preserve the fields of the secrets block the form does not edit (denied
  // tools, supported sinks): the backend replaces the nested model as a whole.
  const secrets = {
    ...(policyState.value?.policy?.secrets || {}),
    ...form.value.secrets
  }
  return persist({ ...form.value, secrets })
}

async function persist(changes) {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const payload = await props.api.ciso.updatePolicy(changes)
    policyState.value = payload
    resetForm()
    notice.value = 'Policy saved: the next gate uses it immediately.'
    emit('changed')
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    saving.value = false
  }
}

async function resetPolicy() {
  if (!props.api || saving.value) return
  if (!window.confirm('Reset the project policy to the defaults?')) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    policyState.value = await props.api.ciso.resetPolicy()
    resetForm()
    const catalogue = await props.api.ciso.rules()
    rules.value = catalogue?.rules || []
    counts.value = catalogue?.counts || {}
    notice.value = 'Policy reset to the defaults.'
    emit('changed')
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    saving.value = false
  }
}

async function toggleRule(rule) {
  if (!props.api || ruleBusy.value) return
  ruleBusy.value = rule.rule_id
  error.value = ''
  notice.value = ''
  try {
    const payload = await props.api.ciso.setRule(rule.rule_id, !rule.enabled)
    Object.assign(rule, payload.rule || {})
    policyState.value = payload.policy || policyState.value
    counts.value = payload.policy?.rules || counts.value
    notice.value = rule.enabled
      ? `Rule enabled: ${rule.rule_id}`
      : payload.protected
        ? `Rule silenced for interactive gates: ${rule.rule_id}. The pre-commit hook still enforces it.`
        : `Rule silenced: ${rule.rule_id}`
    emit('changed')
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    ruleBusy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-3">
    <div v-if="error" class="alert alert-error text-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>{{ error }}
    </div>
    <div v-if="notice" class="alert alert-success text-sm py-2">
      <i class="fa-solid fa-circle-check"></i>{{ notice }}
    </div>
    <div
      v-if="policyState?.silenced_mandatory_rules?.length"
      class="alert alert-warning text-sm py-2"
    >
      <i class="fa-solid fa-shield-halved"></i>
      <span>
        {{ policyState.silenced_mandatory_rules.length }} protection-floor rule(s)
        silenced:
        <code>{{ policyState.silenced_mandatory_rules.join(', ') }}</code>.
        The pre-commit hook still enforces them; restore them to see their
        findings in the interactive gates.
      </span>
    </div>

    <div v-if="loading && !policyState" class="flex items-center gap-2 text-sm text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span> Loading policy and rules…
    </div>

    <template v-if="form">
      <!-- Decision ladder --------------------------------------------------->
      <div class="card bg-base-200/60 border border-base-300">
        <div class="card-body p-4 gap-3">
          <div class="flex items-center justify-between gap-2 flex-wrap">
            <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
              Decision ladder
            </h3>
            <span v-if="dirty" class="badge badge-warning badge-sm">unsaved changes</span>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <label class="flex flex-col gap-1">
              <span class="label-text text-xs mb-1">Warn from</span>
              <select v-model="form.warn_above" class="select select-sm select-bordered w-full">
                <option v-for="level in severities" :key="level" :value="level">{{ severityLabel(level) }}</option>
              </select>
            </label>
            <label class="flex flex-col gap-1">
              <span class="label-text text-xs mb-1">Require approval from</span>
              <select v-model="form.approval_above" class="select select-sm select-bordered w-full">
                <option v-for="level in severities" :key="level" :value="level">{{ severityLabel(level) }}</option>
              </select>
            </label>
            <label class="flex flex-col gap-1">
              <span class="label-text text-xs mb-1">Block from</span>
              <select v-model="form.block_above" class="select select-sm select-bordered w-full">
                <option v-for="level in severities" :key="level" :value="level">{{ severityLabel(level) }}</option>
              </select>
            </label>
          </div>
          <div class="flex flex-wrap gap-4 text-xs">
            <label class="flex items-center gap-2 cursor-pointer">
              <input v-model="form.enabled" type="checkbox" class="toggle toggle-sm toggle-primary" />
              <span>Security subsystem enabled</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input v-model="form.fail_closed" type="checkbox" class="toggle toggle-sm toggle-error" />
              <span>Fail closed when the scanner fails</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input v-model="form.redact_secrets" type="checkbox" class="toggle toggle-sm toggle-info" />
              <span>Redact secrets in payloads</span>
            </label>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <div class="text-xs text-base-content/60 mb-1">Categories that always block above the threshold</div>
              <div class="flex flex-wrap gap-2">
                <label v-for="category in categories" :key="category" class="flex items-center gap-1 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    class="checkbox checkbox-xs"
                    :checked="inList(form.block_categories, category)"
                    @change="toggleList(form.block_categories, category)"
                  />
                  {{ categoryLabel(category) }}
                </label>
              </div>
            </div>
            <div>
              <div class="text-xs text-base-content/60 mb-1">Strict domains (findings block, never approve)</div>
              <div class="flex flex-wrap gap-2">
                <label v-for="domain in domains" :key="`strict-${domain}`" class="flex items-center gap-1 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    class="checkbox checkbox-xs"
                    :checked="inList(form.strict_domains, domain)"
                    @change="toggleList(form.strict_domains, domain)"
                  />
                  {{ domain.replaceAll('_', ' ') }}
                </label>
              </div>
            </div>
          </div>

          <div>
            <div class="text-xs text-base-content/60 mb-1">Severities that always block (any category)</div>
            <div class="flex flex-wrap gap-2">
              <label v-for="level in severities" :key="`block-${level}`" class="flex items-center gap-1 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  class="checkbox checkbox-xs"
                  :checked="inList(form.block_severities, level)"
                  @change="toggleList(form.block_severities, level)"
                />
                {{ severityLabel(level) }}
              </label>
            </div>
          </div>
        </div>
      </div>

      <!-- Scanner options --------------------------------------------------->
      <div class="card bg-base-200/60 border border-base-300">
        <div class="card-body p-4 gap-3">
          <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
            Scanner options
          </h3>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <label class="flex flex-col gap-1">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Entropy threshold</span>
              <input v-model.number="form.entropy_threshold" type="number" step="0.1" min="1" max="8" class="input input-sm input-bordered w-full" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Min secret length</span>
              <input v-model.number="form.min_secret_length" type="number" min="4" max="256" class="input input-sm input-bordered w-full" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Max scan bytes / file</span>
              <input v-model.number="form.max_scan_bytes" type="number" min="1024" max="52428800" class="input input-sm input-bordered w-full" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Excerpt max chars</span>
              <input v-model.number="form.max_excerpt_chars" type="number" min="20" max="2000" class="input input-sm input-bordered w-full" />
            </label>
            <label class="flex flex-col gap-1 sm:col-span-2 xl:col-span-4">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Ignored paths <span class="font-normal normal-case text-base-content/40">(one per line, substring match)</span></span>
              <textarea
                :value="form.ignore_paths.join('\n')"
                rows="4"
                class="textarea textarea-bordered textarea-sm w-full font-mono text-xs"
                placeholder="vendor/&#10;docs/generated/"
                @input="form.ignore_paths = $event.target.value.split('\n').map(item => item.trim()).filter(Boolean)"
              ></textarea>
            </label>
          </div>
          <div class="flex flex-wrap items-center gap-4 rounded-lg bg-base-300/30 px-3 py-2 text-xs">
            <label class="flex items-center gap-2 cursor-pointer">
              <input v-model="form.secrets.enabled" type="checkbox" class="toggle toggle-sm" />
              <span>Secrets resolver</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input v-model="form.secrets.require_grant" type="checkbox" class="toggle toggle-sm" />
              <span>Require per-tool grant</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input v-model="form.secrets.allow_env_source" type="checkbox" class="toggle toggle-sm" />
              <span>Allow env source</span>
            </label>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button class="btn btn-sm btn-primary" :disabled="saving || !dirty" @click="save">
              <span v-if="saving" class="loading loading-spinner loading-xs"></span>
              <i v-else class="fa-solid fa-floppy-disk"></i> Save policy
            </button>
            <button class="btn btn-sm btn-ghost" :disabled="saving || !dirty" @click="resetForm">
              Discard changes
            </button>
            <button class="btn btn-sm btn-ghost text-error ml-auto" :disabled="saving" @click="resetPolicy">
              Reset to defaults
            </button>
          </div>
          <p v-if="policyState?.stored && Object.keys(policyState.stored).length" class="text-[11px] text-base-content/50">
            Project overrides are persisted in <code>.codx/project.json</code>. Defaults stay the baseline.
          </p>
        </div>
      </div>

      <!-- Rule catalogue ---------------------------------------------------->
      <div class="card bg-base-200/60 border border-base-300">
        <div class="card-body p-4 gap-3">
          <div class="flex items-center justify-between gap-2 flex-wrap">
            <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
              Detector rules
            </h3>
            <div class="flex items-center gap-2 text-xs">
              <span class="badge badge-sm badge-ghost">{{ counts.enabled }} enabled</span>
              <span v-if="counts.ignored" class="badge badge-sm badge-warning">{{ counts.ignored }} silenced</span>
              <span class="text-base-content/50">{{ counts.total }} total</span>
            </div>
          </div>

          <div class="grid grid-cols-1 gap-2 md:grid-cols-12 md:items-end">
            <label class="flex min-w-0 flex-col gap-1 md:col-span-5">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Search</span>
              <input v-model="ruleSearch" class="input input-sm input-bordered w-full" placeholder="rule, title or remediation" />
            </label>
            <label class="flex flex-col gap-1 md:col-span-3">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Detector</span>
              <select v-model="ruleSource" class="select select-sm select-bordered w-full">
                <option value="">All detectors</option>
                <option v-for="source in sources" :key="source.value" :value="source.value">{{ source.label }}</option>
              </select>
            </label>
            <label class="flex flex-col gap-1 md:col-span-2">
              <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Scope</span>
              <select v-model="ruleScope" class="select select-sm select-bordered w-full">
                <option value="">All scopes</option>
                <option value="file">File content</option>
                <option value="command">Shell command</option>
              </select>
            </label>
            <label class="flex items-center gap-2 pb-2 text-xs cursor-pointer whitespace-nowrap md:col-span-2">
              <input v-model="showOnlyEnabled" type="checkbox" class="toggle toggle-xs" />
              Enabled only
            </label>
          </div>

          <div class="max-h-[28rem] overflow-y-auto rounded border border-base-300">
            <table class="table table-xs table-pin-rows">
              <thead>
                <tr>
                  <th class="w-16">State</th>
                  <th>Rule</th>
                  <th class="hidden md:table-cell">Severity</th>
                  <th class="hidden lg:table-cell">Detector</th>
                  <th class="hidden xl:table-cell">Remediation</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="rule in filteredRules"
                  :key="rule.rule_id"
                  :class="{ 'opacity-50': !rule.enabled }"
                >
                  <td>
                    <input
                      type="checkbox"
                      class="toggle toggle-xs toggle-primary"
                      :checked="rule.enabled"
                      :disabled="ruleBusy === rule.rule_id"
                      :title="rule.enabled ? 'Silence this rule' : 'Enable this rule'"
                      @change="toggleRule(rule)"
                    />
                  </td>
                  <td>
                    <div class="font-mono text-[11px]">{{ rule.rule_id }}</div>
                    <div class="text-[11px] text-base-content/60">{{ rule.title }}</div>
                    <div class="md:hidden mt-0.5">
                      <span class="badge badge-xs" :class="severityClass(rule.severity)">{{ severityLabel(rule.severity) }}</span>
                      <span class="badge badge-xs badge-ghost">{{ categoryLabel(rule.category) }}</span>
                    </div>
                  </td>
                  <td class="hidden md:table-cell">
                    <span class="badge badge-xs" :class="severityClass(rule.severity)">{{ severityLabel(rule.severity) }}</span>
                    <div class="text-[10px] text-base-content/50">{{ categoryLabel(rule.category) }}</div>
                  </td>
                  <td class="hidden lg:table-cell text-[11px]">
                    {{ rule.source_label }}
                    <div class="text-[10px] text-base-content/50">{{ rule.scope }}</div>
                  </td>
                  <td class="hidden xl:table-cell text-[11px] text-base-content/60 max-w-96">
                    {{ rule.remediation }}
                  </td>
                </tr>
                <tr v-if="!filteredRules.length">
                  <td colspan="5" class="text-center text-xs text-base-content/50 py-4">
                    No rule matches the filters.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="text-[11px] text-base-content/50">
            Rules are deterministic detectors shipped with the security engine. Silencing one removes its findings
            from every gate; the catalog is the single list the scanner actually runs.
          </p>
        </div>
      </div>
    </template>
  </div>
</template>
