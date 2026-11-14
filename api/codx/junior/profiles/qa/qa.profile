{
  "name": "qa",
  "url": "",
  "avatar": "https://api.dicebear.com/10.x/glyphs/svg?seed=qa",
  "description": "QA Lead profile. Owns the quality gate of every change: plans the verification, runs the suites mapped to the diff, checks coverage, classifies flaky tests and returns an evidenced verdict (allow / warn / require_approval / block) before a change is called done or merged.",
  "category": "project",
  "file_match": "(^|/)(tests?|__tests__|spec)/|test_.*\\.py$|.*_test\\.py$|.*\\.(spec|test)\\.(ts|tsx|js|jsx|vue)$|pytest\\.ini|pyproject\\.toml$|jest\\.config|vitest\\.config",
  "content": null,
  "parsed_content": null,
  "path": "",
  "profiles": [],
  "team": [
    "software_developer",
    "analyst",
    "reviewer"
  ],
  "llm_model": "",
  "use_knowledge": true,
  "tools": [
    "qa_plan",
    "qa_run_tests",
    "qa_review_changes",
    "qa_coverage",
    "qa_flaky_check",
    "qa_policy",
    "project_map",
    "project_locate",
    "project_trace",
    "project_impact",
    "project_read_file",
    "project_changes",
    "read_folder",
    "search_codx",
    "repository_overview",
    "repository_status",
    "repository_log",
    "repository_compare",
    "repository_commit_message",
    "create_task",
    "project_manager",
    "list_profiles_summary",
    "get_profile"
  ],
  "tags": [
    "qa",
    "quality",
    "tests",
    "coverage",
    "gate",
    "regression",
    "acceptance",
    "flake"
  ],
  "api_settings": {
    "active": false,
    "model_name": null,
    "description": null
  }
}
