# Initial setup

After [installing codx-junior](/getting-started), open it in your browser.

## 1. First login

Log in with the `admin` user. The first password you type becomes the admin password; you can change it later in **Settings → User account**.

## 2. Start in the workspace

The 0.0.1 installer defaults to the **`core` feature profile**: the company OS
is hidden, so after the first login codx-junior opens the project workspace and
there is **no company wizard**. Add your first repository next (step 4); the
workspace then opens on that project.

If the installation has no project yet, `/projects` lists what was discovered
and offers **New project**.

### Company wizard (full-featured installs only)

Only an installation started with `--features all` (or `--full-featured`)
exposes the company shell. On those installs the first login shows the company
wizard and the product stays locked until it is finished: there is no close
button and no "skip setup", only **Start working** on the last step.

1. **Company** — the default company is pre-created with every discovered project.
   Give it the real name, description, mission, website and brand colour.
2. **Projects** — every discovered project is selected by default. Keep the list
   as it is so projects found later join automatically, or deselect the ones this
   company must not see.
3. **AI models** — optionally pick the default model; you can configure providers
   and keys in the next step.
4. **Finish** — review and press **Start working**. The workspace opens with the
   company selected.

The assistant can be re-run at any time from the user menu → **Setup assistant**;
a re-run never locks the product.

## 3. Add an AI provider and models

Open **Settings → Global settings → AI Models**.

1. Add a **provider**: a name, its OpenAI-compatible API URL and an API key. Any OpenAI-compatible endpoint works, including a local server you run yourself. You can also set the provider's pricing page and its cost in cxjcoins per 1K tokens.
2. Add the **models** you want to use from that provider.
3. Pick the default model for each purpose:

| Setting | Used for |
| --- | --- |
| LLM model | Chats, tasks and agents |
| RAG model | Knowledge search and summaries |
| Embeddings model | Indexing project files |
| Wiki model | Wiki generation |
| Vision model | Understanding images and screenshots |
| Image model | Image generation |

More in [AI providers and models](/features/ai-providers).

## 4. Add a project

From the projects menu, paste a **Git URL** to clone a repository or the **path of a folder** that already exists on the server, then press `+`. The new project becomes the active one.

Each project has its own **Project settings**: models (defaulting to the global ones), knowledge options, scripts, preview URL, wiki, MCP servers and member permissions.

## 5. Index the knowledge

Open **Settings → Knowledge settings**, review the ignore patterns and start indexing. Agents use this index to find the right files. See [Knowledge](/features/knowledge).

## 6. Invite your team

In **Global settings → Users** create users, set their role (`admin` or `user`), grant access to projects and apps, and configure their wallet and spending limits. Users can also sign in with GitHub when an OAuth provider is configured. See [Users and security](/features/users-and-security).

## 7. Create a workspace

Open **Workspaces** and create one from a template to get a dev container with VS Code in the browser, a virtual desktop and your app's preview. See [Workspaces](/features/workspaces).

## A quick tour

* **Home** is the quick chat launcher: start a chat, pick a profile and a model, and reopen recent chats.
* **Kanban** holds your boards and tasks.
* **Messenger** has your teams, channels and direct messages.
* **Workspaces** lists your dev containers and their apps.
* **Analytics** shows AI usage and cost.
* **Settings** has your account, project, knowledge and global settings, plus logs for admins.
