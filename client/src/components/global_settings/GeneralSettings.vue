<script setup>
import ModelSelector from '@/components/ai_settings/ModelSelector.vue'

const weekdays = [
  { value: 'mon', label: 'Mon' },
  { value: 'tue', label: 'Tue' },
  { value: 'wed', label: 'Wed' },
  { value: 'thu', label: 'Thu' },
  { value: 'fri', label: 'Fri' },
  { value: 'sat', label: 'Sat' },
  { value: 'sun', label: 'Sun' }
]
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="flex flex-col gap-4">
      <div class="flex flex-col gap-2 text-xs">
        <div class="text-xl font-bold">Global</div>
        <div class="ml-6 p-2 flex flex-col gap-2">
          <div class="flex items-center">
            <span class="w-1/4">codx-junior Avatar:</span>
            <input v-model="settings.codx_junior_avatar" type="text" class="input input-bordered flex-grow" />
          </div>
          <div class="flex items-center">
            <span class="w-1/4">Projects Root Path:</span>
            <input v-model="settings.projects_root_path" type="text" class="input input-bordered flex-grow" />
          </div>
        </div>
      </div>
      <!-- Human working hours: tasks can be scheduled for the non-working
           hours so autonomous profiles do not collide with the team's day. -->
      <div class="flex flex-col gap-2 text-xs" v-if="settings.working_hours">
        <div class="text-xl font-bold">Human working hours</div>
        <div class="ml-6 p-2 flex flex-col gap-2">
          <label class="flex items-center gap-2">
            <input v-model="settings.working_hours.enabled" type="checkbox" class="checkbox checkbox-sm" />
            <span>Tasks scheduled as "non-working hours" run outside this window</span>
          </label>
          <div class="flex items-center gap-2">
            <span class="w-1/4">Timezone:</span>
            <input v-model="settings.working_hours.timezone" type="text" class="input input-bordered flex-grow"
                   placeholder="Europe/Madrid" />
          </div>
          <div class="flex items-center gap-2">
            <span class="w-1/4">Start / end:</span>
            <input v-model="settings.working_hours.start" type="time" class="input input-bordered w-32" />
            <input v-model="settings.working_hours.end" type="time" class="input input-bordered w-32" />
          </div>
          <div class="flex items-center gap-2">
            <span class="w-1/4">Days:</span>
            <div class="flex flex-wrap gap-2">
              <label v-for="day in weekdays" :key="day.value"
                     class="flex items-center gap-1 rounded-lg border border-base-100 px-2 py-1">
                <input type="checkbox" class="checkbox checkbox-xs"
                       :value="day.value" v-model="settings.working_hours.days" />
                <span>{{ day.label }}</span>
              </label>
            </div>
          </div>
          <p class="text-[11px] opacity-50">
            "Non-working hours" resolves to the next moment this window closes (or now,
            when humans are already offline). The value is stored as the task's
            <code>wake_at</code>.
          </p>
        </div>
      </div>
      <div class="flex flex-col gap-2 text-xs">
        <div class="text-xl font-bold">Git</div>
        <div class="ml-6 p-2 flex flex-col gap-2">
          <div class="flex items-center">
            <span class="w-1/4">Username:</span>
            <input v-model="settings.git.username" type="text" class="input input-bordered flex-grow" />
          </div>
          <div class="flex items-center">
            <span class="w-1/4">Email:</span>
            <input v-model="settings.git.email" type="text" class="input input-bordered flex-grow" />
          </div>
        </div>
      </div>
      <div class="flex flex-col gap-2 text-xs">
        <div class="text-xl font-bold">AI</div>
        <div class="ml-6 p-2 flex flex-col gap-2">
          <div class="flex items-center">
            <span class="w-1/4">Log AI:</span>
            <input v-model="settings.log_ai" type="checkbox" class="checkbox" />
          </div>
          <div class="font-bold">Embeddings</div>
          <div class="text-xs">Converts project's documents into embeddings for knowledge search</div>
          <div class="flex flex-col gap-2">
            <div class="flex items-center">
              <span class="p-6 w-1/4">Model:</span>
              <ModelSelector v-model="settings.embeddings_model" />
            </div>
          </div>
          <div class="font-bold">Knowledge search</div>
          <div class="text-xs">Scores knowledge results based on user's request</div>
          <div class="flex flex-col gap-2">
            <div class="flex items-center">
              <span class="p-6 w-1/4">Model:</span>
              <ModelSelector v-model="settings.rag_model" />
            </div>
          </div>
          <div class="font-bold">Reasoning</div>
          <div class="text-xs">Default Reasoning model</div>
          <div class="flex flex-col gap-2">
            <div class="flex items-center">
              <span class="p-6 w-1/4">Model:</span>
              <ModelSelector v-model="settings.llm_model" />
            </div>
          </div>
          <div class="font-bold">WIKI</div>
          <div class="text-xs">Generates wiki documents from project's knowledge</div>
          <div class="flex flex-col gap-2">
            <div class="flex items-center">
              <span class="p-6 w-1/4">Model:</span>
              <ModelSelector v-model="settings.wiki_model" />
            </div>
          </div>
          <div class="font-bold">Image generation</div>
          <div class="text-xs">Default model for generating images from text. A project overrides it with its own image generation model.</div>
          <div class="flex flex-col gap-2">
            <div class="flex items-center">
              <span class="p-6 w-1/4">Model:</span>
              <ModelSelector v-model="settings.image_generation_model" />
            </div>
          </div>
          <div class="font-bold">Image vision</div>
          <div class="text-xs">Default model for creating text descriptions from images. A project overrides it with its own image vision model.</div>
          <div class="flex flex-col gap-2">
            <div class="flex items-center">
              <span class="p-6 w-1/4">Model:</span>
              <ModelSelector v-model="settings.image_vision_model" />
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  props: {
    settings: {
      type: Object,
      required: true
    }
  }
}
</script>