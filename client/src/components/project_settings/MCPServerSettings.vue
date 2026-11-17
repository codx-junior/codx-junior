<template>
  <div class="border border-base-100 rounded-lg p-6 space-y-4">
    <div class="flex justify-between items-start gap-4">
      <div class="flex-1 space-y-4">
        <div>
          <label class="label-text block font-semibold mb-2">Server name</label>
          <input
            v-model="server.name"
            type="text"
            placeholder="e.g., My MCP Server"
            class="input input-bordered w-full"
          />
        </div>
        <div>
          <label class="label-text block font-semibold mb-2">Server URL</label>
          <input
            v-model="server.url"
            type="text"
            placeholder="e.g., http://localhost:3000/mcp"
            class="input input-bordered w-full"
          />
        </div>
        <div>
          <label class="label-text block font-semibold mb-2">API Key</label>
          <input
            v-model="server.api_key"
            type="password"
            placeholder="Server API key (sent as a Bearer token)"
            class="input input-bordered w-full"
          />
        </div>

        <details class="rounded-lg border border-base-content/10 p-3">
          <summary class="cursor-pointer text-xs font-semibold text-primary">
            Advanced connection
          </summary>
          <div class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label class="flex flex-col gap-1">
              <span class="text-[10px] uppercase tracking-wider text-base-content/40">Transport</span>
              <select v-model="server.transport" class="select select-sm select-bordered">
                <option value="http">Streamable HTTP</option>
              </select>
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[10px] uppercase tracking-wider text-base-content/40">Timeout (seconds)</span>
              <input
                v-model.number="server.timeout_seconds"
                type="number"
                min="1"
                max="120"
                class="input input-sm input-bordered"
              />
            </label>
            <label class="flex flex-col gap-1 sm:col-span-2">
              <span class="text-[10px] uppercase tracking-wider text-base-content/40">
                Tool allowlist <span class="normal-case text-base-content/30">(empty = every tool)</span>
              </span>
              <input
                :value="(server.tool_allowlist || []).join(', ')"
                type="text"
                class="input input-sm input-bordered"
                placeholder="get_customer, list_invoices, create_refund"
                @change="setAllowlist($event.target.value)"
              />
            </label>
          </div>
        </details>
      </div>
      <button class="btn btn-error" title="Remove this MCP server" @click="$emit('remove')">
        <i class="fa-solid fa-trash"></i>
      </button>
    </div>

    <div class="flex items-center gap-3 pt-2 border-t border-base-100">
      <input v-model="server.active" type="checkbox" class="toggle" />
      <label class="label-text">Active</label>
      <button
        type="button"
        class="btn btn-sm btn-ghost gap-1 ml-auto"
        :disabled="testing || !server.url"
        @click="testConnection"
      >
        <span v-if="testing" class="loading loading-spinner loading-xs"></span>
        <i v-else class="fa-solid fa-plug-circle-check"></i>
        Test connection
      </button>
    </div>

    <div v-if="testError" class="text-xs text-error">
      <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ testError }}
    </div>
    <div v-else-if="testResult" class="rounded-lg bg-base-200/60 p-3 text-xs">
      <div class="font-semibold text-success">
        <i class="fa-solid fa-circle-check mr-1"></i>
        Connected — {{ (testResult.tools || []).length }} tool(s)
      </div>
      <div v-if="(testResult.tools || []).length" class="flex flex-wrap gap-1 mt-2">
        <code
          v-for="tool in testResult.tools.slice(0, 12)"
          :key="tool.exposed_name || tool.name"
          class="badge badge-xs badge-ghost"
          :title="tool.description"
        >{{ tool.exposed_name || tool.name }}</code>
        <span v-if="testResult.tools.length > 12" class="badge badge-xs">+{{ testResult.tools.length - 12 }}</span>
      </div>
      <div class="text-[10px] text-base-content/40 mt-1">
        Tested before save. Add the tool names to the profile that should use them.
      </div>
    </div>
  </div>
</template>

<script>
export default {
  props: {
    modelValue: {
      type: Object,
      required: true
    }
  },
  emits: ['update:modelValue', 'remove'],
  data() {
    return {
      testing: false,
      testResult: null,
      testError: ''
    }
  },
  computed: {
    server: {
      get() {
        return this.modelValue
      },
      set(value) {
        this.$emit('update:modelValue', value)
      }
    }
  },
  watch: {
    // A changed URL/key invalidates the previous test result.
    'server.url'() { this.resetTest() },
    'server.api_key'() { this.resetTest() }
  },
  methods: {
    resetTest() {
      this.testResult = null
      this.testError = ''
    },
    setAllowlist(value) {
      this.server.tool_allowlist = String(value || '')
        .split(',')
        .map(name => name.trim())
        .filter(Boolean)
    },
    async testConnection() {
      const api = this.$project?.$api?.mcp
      if (!api) {
        this.testError = 'MCP API not available'
        return
      }
      this.testing = true
      this.testResult = null
      this.testError = ''
      try {
        this.testResult = await api.test({
          name: this.server.name || 'mcp',
          url: this.server.url,
          api_key: this.server.api_key || '',
          transport: this.server.transport || 'http',
          headers: this.server.headers || {},
          timeout_seconds: Number(this.server.timeout_seconds || 15),
          protocol_version: this.server.protocol_version || '2025-03-26',
          tool_allowlist: this.server.tool_allowlist || []
        })
      } catch (ex) {
        this.testError = ex?.message || 'Could not connect to this MCP server'
      } finally {
        this.testing = false
      }
    }
  }
}
</script>
