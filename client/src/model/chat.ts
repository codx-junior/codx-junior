export interface Message {
  doc_id?: string
  role: string
  task_item: string
  content: string
  think?: string
  hide: boolean
  is_answer: boolean
  improvement: boolean
  created_at: string
  updated_at: string
  images: string[]
  files: string[]
  meta_data?: Record<string, any>
  profiles: string[]
  user?: string
  knowledge_topics: string[]
  done?: boolean
  is_thinking?: boolean
  disable_knowledge?: boolean
  read_by: string[]
  error?: string
  // Project the message is addressed to. When present and different from the
  // chat's working project, the message is rendered with that project's icon.
  project_id?: string
  // Events recorded while the response was generated. They are what the turn
  // timeline and the details panel of the message header read:
  //  · tool_events      one per tool call (identity, args, result, timestamps)
  //  · lifecycle_events one per agent run (started / done / error)
  //  · phase_events     the WAITING of the turn — model request, streaming,
  //                     notices, and the interruption written by the recovery
  //                     worker when the process died. Without them a turn that
  //                     is waiting for the AI looks the same as a dead one.
  tool_events?: ToolEvent[]
  lifecycle_events?: LifeCycleEvent[]
  phase_events?: PhaseEvent[]
}

export interface ToolEvent {
  tool: string
  tool_call_id: string
  status: 'running' | 'done' | 'error' | string
  request?: Record<string, any> | null
  response?: string | null
  output?: string | null
  duration_ms?: number | null
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
  cached?: boolean | null
}

export interface LifeCycleEvent {
  status: 'running' | 'done' | 'error' | string
  run_id: string
  duration_ms?: number | null
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface PhaseEvent {
  kind: 'llm_request' | 'llm_response' | 'notice' | 'wallet_check' | 'interrupted' | string
  status: 'running' | 'done' | 'error' | string
  label?: string | null
  detail?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  meta?: Record<string, any> | null
}

export interface ChatId {
  chat_id: string
  project_id: string
}

export interface KanbanColumn {
  id: string
  name: string
}

export interface Chat {
  id?: string
  doc_id?: string
  project_id?: string
  owner_project_id?: string
  parent_id?: string
  parent_owner_project_id?: string
  parent_project_id?: string
  child_index?: number
  message_id?: string
  status: string
  file_list: string[]
  check_lists?: Record<string, any>[]
  profiles: string[]
  users: string[]
  name: string
  pinned?: boolean
  description: string
  messages: Message[]
  created_at: string
  updated_at: string
  mode: string
  kanban_id: string
  column_id: string
  board: string
  column: string
  columns: KanbanColumn[]
  chat_index?: number
  url: string
  branch: string
  file_path: string
  llm_model?: string
  visibility?: string
  remote_url?: string
  knowledge_topics: string[]
  chat_links: ChatId[]
  pr_view?: Record<string, any>
  temp?: boolean
}
