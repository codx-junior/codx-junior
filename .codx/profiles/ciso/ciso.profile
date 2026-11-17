{
  "name": "ciso",
  "url": "",
  "avatar": "https://api.dicebear.com/10.x/glyphs/svg?seed=ciso",
  "description": "CISO profile. Owns the security of everything the platform produces: audits processes for secrets and malicious code, reviews every change before it is committed or merged, validates generated files, commands and containers, and blocks anything that crosses a trust boundary without evidence.",
  "category": "project",
  "file_match": "(\\.env|\\.pem|\\.key|Dockerfile|docker-compose|\\.github/workflows/|\\.gitlab-ci|nginx|traefik)",
  "content": null,
  "parsed_content": null,
  "path": "/home/codx-junior/codx-junior/api/codx/junior/profiles/ciso/ciso.profile",
  "profiles": [],
  "team": [
    "software_developer",
    "devops",
    "reviewer",
    "analyst"
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
    "security_audit",
    "security_scan_content",
    "security_review_changes",
    "security_check_command",
    "security_redact",
    "security_policy",
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
    "fetch_webpage",
    "create_task",
    "project_manager",
    "list_profiles_summary",
    "get_profile"
  ],
  "tags": [
    "security",
    "ciso",
    "audit",
    "secrets",
    "malicious-code",
    "prompt-injection",
    "compliance",
    "guardrails",
    "supply-chain"
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
    "session": "single",
    "board": "Agent: ciso",
    "boards": [
      "IT team"
    ],
    "interval_minutes": 240,
    "mode": "active",
    "budget_cxjcoins": 0.0,
    "daily_budget_cxjcoins": 0.0,
    "max_runtime_minutes": 20,
    "max_turns": 4,
    "max_agenda": 3,
    "max_tasks": 40,
    "include_done": false,
    "instructions": "Review only the cards in \"Review\" on the \"IT team\" board whose diff touches auth, secrets, dependencies or shell. Run security_review_changes and post the verdict (secrets, malicious code, injected instructions) as a comment. A block is reported, never worked around.",
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