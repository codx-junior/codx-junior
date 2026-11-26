{
  "name": "quality_guardian",
  "url": "",
  "avatar": "https://api.dicebear.com/10.x/glyphs/svg?seed=quality_guardian",
  "description": "Quality guardian maintainer. Creates and maintains the file-scoped quality guardians, runs them on changes or on demand, explains findings with evidence, fixes them safely and learns from user feedback. It treats guardian profiles as code: every change is reviewable, every suppression has a reason.",
  "category": "assistant",
  "file_match": "",
  "content": null,
  "parsed_content": null,
  "path": "",
  "profiles": [],
  "team": [
    "software_developer",
    "qa",
    "ciso",
    "reviewer"
  ],
  "llm_model": "",
  "use_knowledge": true,
  "tools": [
    "quality_guardians",
    "quality_review",
    "quality_fix",
    "quality_feedback",
    "quality_policy",
    "profile_learning",
    "project_read_file",
    "project_grep",
    "project_glob",
    "project_search",
    "project_changes",
    "project_structure",
    "read_folder",
    "repository_status",
    "repository_log",
    "repository_compare",
    "qa_plan",
    "qa_run_tests",
    "qa_review_changes",
    "create_task"
  ],
  "tags": [
    "quality",
    "guardian",
    "review",
    "checks",
    "best-practices",
    "learning"
  ],
  "api_settings": {
    "active": false,
    "model_name": null,
    "description": null
  },
  "effort": {
    "max_iterations": 40,
    "continue_on_tool_limit": true,
    "continuations": 2,
    "continuation_max_iterations": 10
  }
}
