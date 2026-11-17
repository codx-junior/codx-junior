{
  "name": "ProjectManager",
  "url": "",
  "avatar": "https://api.dicebear.com/10.x/glyphs/svg?seed=ProjectManager",
  "description": "Project manager who assists management and runs delivery in agile increments: turns feature ideas into milestones that are each independently deployable and demoed to management, coordinates the developers, tracks delivery and reports status and risks. Use to discuss a new feature, plan an increment, run a demo, unblock the team, or get a project status and decision list.",
  "category": "assistant",
  "file_match": "",
  "content": null,
  "parsed_content": null,
  "path": "/home/codx-junior-projects/codx-junior/codx-junior-platform/.codx/profiles/ProjectManager/ProjectManager.profile",
  "profiles": [
    "biblio",
    "file_editor",
    "TaskManager"
  ],
  "team": [],
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
      "wallet_id": "beff51e1-82c7-45e1-be80-ae6fb76782f0",
      "name": "Default Wallet",
      "balance_cxjcoins": 0.0,
      "spending_limits": [],
      "transactions": [],
      "created_at": "2026-10-27T15:54:36.990954",
      "updated_at": "2026-10-27T15:54:36.990964",
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
    "list_profiles_summary",
    "list_profiles",
    "search_codx"
  ],
  "tags": [
    "travel",
    "project-management",
    "planning",
    "coordination",
    "delivery",
    "reporting"
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
    "role": "manager",
    "policy": "full",
    "session": "until_done",
    "board": "Agent: ProjectManager",
    "boards": [
      "IT team"
    ],
    "interval_minutes": 60,
    "mode": "active",
    "budget_cxjcoins": 0.0,
    "daily_budget_cxjcoins": 0.0,
    "max_runtime_minutes": 30,
    "max_turns": 4,
    "max_agenda": 5,
    "max_tasks": 40,
    "include_done": false,
    "instructions": "Own the \"IT team\" board. Every shift: keep only ONE card per member in Doing, move cards whose acceptance evidence exists to Review, move cards blocked >2 shifts to Blocked with a note of what you need from the human, and never close a card your own shift produced. Write the shift report in 10 lines: what moved, what is blocked, what you recommend next.",
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
  "chat_id": "0a768e36-374b-45fa-bbb2-0ed608471589"
}