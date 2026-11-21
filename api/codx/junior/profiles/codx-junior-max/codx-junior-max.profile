{
  "name": "codx-junior-max",
  "url": "",
  "avatar": "https://api.dicebear.com/10.x/glyphs/svg?seed=codx-junior-max",
  "description": "codx-junior-max harness (MAX effort): the high-effort tier for long or complex work \u2014 multi-file refactors, migrations, full-suite validation, long research. It runs at reasoning_effort=max and inherits everything from codx-junior (plain language, 4-section close, mandatory validation, no permission questions) while adding a much larger budget, orchestration and the heavy validation gate. It keeps executing batches until the objective is met or a hard blocker is reported.",
  "category": "assistant",
  "file_match": "",
  "content": null,
  "path": "",
  "profiles": [
    "codx-junior"
  ],
  "team": [
    "software_developer",
    "analyst",
    "reviewer",
    "TaskManager",
    "ProjectManager",
    "devops",
    "ciso",
    "qa",
    "qa_automation",
    "qa_explorer",
    "repository",
    "web_master",
    "workspace",
    "browser",
    "wiki",
    "teacher",
    "support"
  ],
  "llm_model": "",
  "use_knowledge": true,
  "tools": [
    "*"
  ],
  "tags": [
    "harness",
    "orchestration",
    "max-effort",
    "long-running"
  ],
  "api_settings": {
    "active": false,
    "model_name": null,
    "description": null
  },
  "project_id": "",
  "effort": {
    "reasoning_effort": "max",
    "max_iterations": 80,
    "max_tool_calls": 12,
    "max_tool_rounds": 160,
    "continue_on_tool_limit": true,
    "continuations": 6,
    "continuation_max_iterations": 20,
    "continuation_max_calls": 8,
    "wall_clock_seconds": 14400.0,
    "target_seconds": 7200.0,
    "stop_on_wall_clock": false,
    "validation": "full+coverage+security"
  }
}
