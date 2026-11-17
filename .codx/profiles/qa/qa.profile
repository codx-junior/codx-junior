{
  "name": "qa",
  "url": "",
  "avatar": "https://api.dicebear.com/10.x/glyphs/svg?seed=qa",
  "description": "QA Lead profile. Owns the quality gate of every change: plans the verification, runs the suites mapped to the diff, checks coverage, classifies flaky tests and returns an evidenced verdict (allow / warn / require_approval / block) before a change is called done or merged.",
  "category": "project",
  "file_match": "(^|/)(tests?|__tests__|spec)/|test_.*\\.py$|.*_test\\.py$|.*\\.(spec|test)\\.(ts|tsx|js|jsx|vue)$|pytest\\.ini|pyproject\\.toml$|jest\\.config|vitest\\.config",
  "content": null,
  "parsed_content": null,
  "path": "/home/codx-junior/codx-junior/api/codx/junior/profiles/qa/qa.profile",
  "profiles": [],
  "team": [
    "software_developer",
    "analyst",
    "reviewer"
  ],
  "llm_model": "",
  "use_knowledge": true,
  "user": {
    "username": "",
    "email": "",
    "avatar": "",
    "theme": "dim",
    "projects": [],
    "role": "user",
    "companies": [],
    "language": "auto",
    "workspace_mode": "auto",
    "token": "",
    "disabled": false,
    "github": "",
    "apps": [],
    "api_key": "",
    "env": {},
    "wallet": {
      "wallet_id": "bd04c890-4f03-4559-a114-ab9cbbc17e1f",
      "name": "Default Wallet",
      "balance_cxjcoins": 0.0,
      "spending_limits": [],
      "transactions": [],
      "created_at": "2026-11-17T07:11:39.143499",
      "updated_at": "2026-11-17T07:11:39.143506",
      "enabled": true
    },
    "github_admin": false,
    "github_only": false,
    "token_limit_rules": [],
    "token_limit_requests": [],
    "is_company_mode": false,
    "company_ids": []
  },
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
  "effort": null,
  "api_settings": {
    "active": false,
    "model_name": null,
    "description": null
  },
  "guardrails": {
    "enabled": false,
    "scope": null,
    "scope_keywords": [],
    "scope_similarity_threshold": 0.35,
    "allowed_tools": [],
    "refusal_message": "I can only answer questions within my configured scope.",
    "risk_threshold": 10.0,
    "use_llm_classifier": false
  },
  "chat_mode": null,
  "actor": {
    "enabled": true,
    "role": "coworker",
    "policy": "full",
    "session": "until_done",
    "board": "Agent: qa",
    "boards": [
      "IT team"
    ],
    "interval_minutes": 120,
    "mode": "active",
    "budget_cxjcoins": 0.0,
    "daily_budget_cxjcoins": 0.0,
    "max_runtime_minutes": 30,
    "max_turns": 4,
    "max_agenda": 3,
    "max_tasks": 40,
    "include_done": false,
    "instructions": "Only work cards in \"Review\" on the \"IT team\" board. Run qa_review_changes on the card's diff and post the verdict (allow/warn/require_approval/block) plus failing test ids as a comment. Do not edit product code: if the verdict is not allow, move the card back to Doing with the exact gap.",
    "wake_at": "",
    "notify": true,
    "schedule": {
      "enabled": true,
      "timezone": "Europe/Madrid",
      "windows": [
        {
          "days": [
            "mon",
            "tue",
            "wed",
            "thu",
            "fri"
          ],
          "start": "09:00",
          "end": "19:00"
        }
      ]
    }
  },
  "autonomy": null,
  "project_id": "d4e4b4a9-2281-4970-bff1-f845b4d98456",
  "chat_id": ""
}