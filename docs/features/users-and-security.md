# Users and security

## Users and roles

Admins manage users in **Global settings → Users**. Each user has:

* a username, email, avatar and theme,
* an **assistant language** (`auto`, `en`, `es`, …) used by chat answers and
  [voice calls](/features/voice-conversation),
* a **role**: `admin` or `user`,
* **project permissions**, optionally inherited by sub-projects,
* access to specific **workspace apps**,
* personal **environment variables**,
* a [wallet and token limits](/features/budget-and-analytics).

Users can be disabled without being deleted.

## Signing in

* **Username and password.** The `admin` user sets its password on the first login.
* **GitHub OAuth.** Configure an OAuth provider in **Global settings → OAuth**. GitHub accounts listed as admins get the admin role. A user can be restricted to GitHub-only login.

## Security posture

The [CISO view](/features/ciso) shows the project's security posture, runs
background analyses and lets an operator review findings, the detector rules and
the policy (block/approval/warn thresholds, ignored paths and silenced rules).
The same deterministic gates protect file writes, commits, shell commands,
containers, knowledge ingestion and model output.

## Protecting apps

Every app routed by Traefik (workspace apps, LocalAI, the Traefik dashboard) goes through codx-junior's **forward-auth** endpoint. Only signed-in users with access to that app can open it.

## Settings history

Global settings are versioned by section. Admins can review previous versions and roll back a change.
