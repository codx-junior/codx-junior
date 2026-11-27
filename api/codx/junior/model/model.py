import os
import regex

from pydantic import BaseModel, Field, constr, validator
from enum import Enum
from datetime import datetime

from typing import List, Dict, Union, Optional

# ---------------------------------------------------------------------------
# Import user models from dedicated module
# ---------------------------------------------------------------------------
from codx.junior.model.user import (
    CodxUserLogin,
    CodxUserProjectProfile,
    CodxUser,
)

# ---------------------------------------------------------------------------
# Import AI models from dedicated module
# ---------------------------------------------------------------------------
from codx.junior.model.ai_model import (
    AIProvider,
    AILLMModelSettings,
    AIEmbeddingModelSettings,
    AIModelType,
    AIModel,
    AISettings,
)

# ---------------------------------------------------------------------------
# Import profile models from dedicated module
# ---------------------------------------------------------------------------
from codx.junior.model.profile import (
    Profile,
    ProfileApiSettings,
)

# ---------------------------------------------------------------------------
# Import workspace models from dedicated module
# ---------------------------------------------------------------------------
from codx.junior.model.workspace import (
    Workspace,
    WorkspaceApp,
)

# ---------------------------------------------------------------------------
# Import image models from dedicated module
# ---------------------------------------------------------------------------
from codx.junior.model.image_model import (
    ImageGenerationRequest,
    ImageAnalysisRequest,
    ImageMetadata,
    ImageGenerationResponse,
    ImageAnalysisResponse,
)

# ---------------------------------------------------------------------------
# Traefik custom rules (global settings live here; project rules live in
# CODXJuniorSettings). The storage location is the rule scope.
# ---------------------------------------------------------------------------
from codx.junior.traefik.model import TraefikRule


class ImageUrl(BaseModel):
    url: str = Field(default="")

class Content(BaseModel):
    type: str = Field(default='text')
    text: str = Field(default=None)
    image_url: ImageUrl = Field(default=None)

class ChatMessage(BaseModel):
    role: str = Field(default='')
    content: List[Content] = Field(default=[])

class Column(BaseModel):
    name: str = Field(default='')
    chat_ids: List[str] = Field(default=[])
    project_id: str = Field(default='')

class Board(BaseModel):
    name: str = Field(default='')
    description: str = Field(default='')
    remote_url: str = Field(default='')
    bookmark: Optional[bool] = Field(default=False)
    columns: List[Column] = Field(default=[])
    project_id: str = Field(default='')

class Logprobs(BaseModel):
    tokens: List[str]
    token_logprobs: List[float]
    top_logprobs: List[Dict[str, float]]
    text_offset: List[int]

class KnowledgeReloadPath(BaseModel):
    path: str

class KnowledgeDeleteSources(BaseModel):
    sources: List[str]

class KnowledgeSearch(BaseModel):
    search_term: str
    search_type: str = Field(default=None)
    document_search_type: str = Field(default=None)
    document_count: int = Field(default=None)
    document_cutoff_score: float = Field(default=None)
    document_cutoff_rag: float = Field(default=None)

class Tool(BaseModel):
    name: str = Field(default="")
    description: str = Field(default="")

class CodxJuniorBaseTools(BaseModel):
    knowledge: Tool = Tool(name="knowledge", description="Project's knowledge search")

class CommandTool(Tool):
    command: Optional[str] = Field(description="Command", default=None)

class PRView(BaseModel):
    from_branch: Optional[str] = Field(default="")
    to_branch: Optional[str] = Field(default="")

class Document(BaseModel):
    id: int = Field(default=None)
    page_content: str
    metadata: dict

class LiveEdit(BaseModel):
    chat_name: str
    html: str
    url: str
    message: str

class OpenAISettings(BaseModel):
    openai_api_url: Optional[str] = Field(default="")
    openai_api_key: Optional[str] = Field(default="")
    openai_model: Optional[str] = Field(default="gpt-4o")
    
class AnthropicAISettings(BaseModel):
    anthropic_api_url: Optional[str] = Field(default="")
    anthropic_api_key: Optional[str] = Field(default="")
    anthropic_model: Optional[str] = Field(default="claude-3-5-sonnet-20240620")

class MistralAISettings(BaseModel):
    mistral_api_url: Optional[str] = Field(default="")
    mistral_api_key: Optional[str] = Field(default="")
    mistral_model: Optional[str] = Field(default="codestral-latest")

class GitSettings(BaseModel):
    username: Optional[str] = Field(default="")
    email: Optional[str] = Field(default="")

class ProjectScript(BaseModel):
    name: str = Field(description="Script name")
    description: str = Field(description="Script name", default="")
    script: str = Field(description="Bash script", default="")
    status: str = Field(description="Script status: running, stopped, error", default="stopped")
    background: bool = Field(description="Script runs in background", default=False)
    restart: bool = Field(description="Script must be restarted if stopped", default=False)
    pid_file_path: str = Field(default="")
    engine: str = Field(default="bash")

class Bookmark(BaseModel):
    name: str
    icon: Optional[str] = Field(default="")
    title: Optional[str] = Field(default="")
    url: Optional[str] = Field(default="")
    port: Optional[int] = Field(default=None)

class AgentSettings(BaseModel):
    max_agent_iteractions: int = 4

class OAuthProvider(BaseModel):
    name: str = Field(default="")
    client_id: str = Field(default="")
    secret: str = Field(default="")
    token_url: str = Field(default="")

class MCPServer(BaseModel):
    """Model representing a Model Context Protocol (MCP) server configuration.

    A project registers the servers it can use; the agent discovers their tools
    at runtime and exposes them to profiles as ``mcp_<server>_<tool>``. The API
    key is sent as a Bearer token; ``headers`` covers servers that need a
    different scheme (for example an ``X-Api-Key`` header). Keep credentials in
    the MCP server / secret store rather than in a profile document.
    """
    name: str = Field(description="Name of the MCP server")
    url: str = Field(description="URL/endpoint of the MCP server")
    api_key: Optional[str] = Field(default="", description="API key for authentication")
    active: bool = Field(default=True, description="Whether the MCP server is active")
    transport: str = Field(
        default="http",
        description="Transport: http (streamable HTTP), sse or stdio",
    )
    tool_allowlist: List[str] = Field(
        default_factory=list,
        description=(
            "MCP tool names exposed to profiles (empty = every tool). Names are "
            "the server's native names, not the namespaced mcp_* names."
        ),
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Extra HTTP headers for the MCP endpoint (non-secret values only)",
    )
    timeout_seconds: float = Field(
        default=30.0, description="Maximum time for one MCP request"
    )
    protocol_version: str = Field(
        default="2025-03-26", description="MCP protocol version to negotiate"
    )
    command: str = Field(
        default="", description="Command for the optional stdio transport"
    )
    args: List[str] = Field(
        default_factory=list, description="Arguments for the optional stdio transport"
    )

# Define the PluginArgument model
class PluginArgument(BaseModel):
    name: str
    description: str
    default_value: str

# Define the Plugin model with an async property
class Plugin(BaseModel):
    plugin_id: str
    name: str
    description: str
    module_path: str
    plugin_path: str
    method: str
    arguments: List[PluginArgument]
    roles: List[str]
    extends: List[str]
    image: Optional[str] = Field(default=None)
    async_: bool = Field(default=False, alias="async")  # Use alias for async


class HumanWorkingHours(BaseModel):
    """
    When humans (the people using codx-junior) are working.

    Tasks can be scheduled for the **non-working hours** so autonomous agents
    do not compete with the daily work of the team. The window is
    installation-wide: ``enabled`` turns the concept on, ``timezone`` is the
    IANA zone used to read it and ``days``/``start``/``end`` describe the daily
    window. Outside it (and on weekends by default) is "non-working".

    Attributes:
        enabled: Whether the working window is enforced.
        timezone: IANA zone (default UTC).
        days: Weekdays humans work (``mon``..``sun``).
        start: Opening time ``HH:MM``.
        end: Closing time ``HH:MM``.
    """

    enabled: bool = Field(default=True)
    timezone: str = Field(default="UTC")
    days: List[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"])
    start: str = Field(default="09:00")
    end: str = Field(default="18:00")


class GlobalSettings(BaseModel):
    log_ai: bool = Field(default=True)
    
    embeddings_model: str = Field(default="")
    llm_model: str = Field(default="")
    rag_model: str = Field(default="")
    wiki_model: str = Field(default="")
    guardrail_model: str = Field(default="")

    # Image models. The names are the SAME in the global and the project
    # settings: here they are the installation default, in
    # CODXJuniorSettings they override it per project.
    #
    # Generating an image (text -> image) and analysing one (image -> text) are
    # two different capabilities, so each one has its own setting and neither
    # ever falls back to the other's model.
    #
    # Both default to "" (disabled) unlike the text models above: an image
    # capability cannot fall back to a text model, and a non-empty default
    # would make is_image_generation_enabled() always true.
    image_generation_model: str = Field(
        default="",
        description=(
            "Default image generation model (text -> image). A project overrides "
            "it with its own image_generation_model"
        )
    )
    image_vision_model: str = Field(
        default="",
        description=(
            "Default image vision model (image -> text). A project overrides it "
            "with its own image_vision_model"
        )
    )

    git: GitSettings = Field(default=GitSettings())

    # When humans work. Used to resolve the "non-working-hours" task schedule:
    # autonomous work is moved out of this window so profiles never collide
    # with the team's daily work.
    working_hours: HumanWorkingHours = Field(default_factory=HumanWorkingHours)

    agent_settings: AgentSettings = Field(description="Agent settings", default=AgentSettings())

    projects_root_path: Optional[str] = Field(default=None)
    
    log_ignore: List[str] = Field(default=[])

    codx_junior_avatar: Optional[str] = Field(default="https://img.daisyui.com/images/stock/photo-1534528741775-53994a69daeb.webp")

    enable_file_manager: Optional[bool] = Field(default=False)

    project_scripts: Optional[List[ProjectScript]] = Field(default=[])

    bookmarks: List[Bookmark] = Field(default=[
    ])

    ai_providers: List[AIProvider] = []

    ai_models: List[AIModel] = []

    users: Optional[List[CodxUser]] = Field(default=[CodxUser(username="admin", role="admin", avatar="/only_icon.png")])
    user_logins: Optional[List[CodxUserLogin]] = Field(default=[])
    # JWT/HMAC signing secret. Deliberately empty: the previous default
    # ("codx-junior-rules") was a 17-byte constant published in the source tree,
    # shared by every installation and below the 32 bytes RFC 7518 requires for
    # HS256. ``codx.junior.global_settings.ensure_signing_secret()`` generates a
    # random >= 32-byte secret and persists it on first use / at startup.
    secret: Optional[str] = Field(
        default="",
        description=(
            "JWT/HMAC signing secret, at least "
            "codx.junior.security.secret_key.MIN_SECRET_BYTES bytes; "
            "auto-generated when empty or too short"
        ),
    )

    # Dev-container workspaces are a post-0.0.1 feature and are no longer
    # configured from Global Settings. A fresh installation starts with none
    # instead of seeding the old "codx-junior" default workspace.
    workspaces: Optional[List[Workspace]] = Field(default=[])
    workspace_start_port: Optional[int] = Field(default=16000)
    workspace_end_port: Optional[int] = Field(default=17000)
    workspace_docker_settings: Optional[dict] = Field(default={})

    # Custom Traefik routers defined by an admin. Project rules live in each
    # project's `.codx/project.json` instead, so the same UI component can
    # target either storage.
    traefik_rules: List[TraefikRule] = Field(default_factory=list)

    oauth_providers: Optional[List[OAuthProvider]] = Field(default=[])

    env: Optional[dict] = Field(default={})

    plugins: List[Plugin] = Field(default=[])

    chat_global_instructions: str = Field(default="")
    
    
class Screen(BaseModel):
    resolution: str = Field(default='')
    resolutions: List[str] = Field(default=[
      "1920x1080",
      "1920x1200",
      "1600x1200",
      "1680x1050",
      "1400x1050",
      "1360x768",
      "1280x1024",
      "1280x960",
      "1280x800",
      "1280x720",
      "1024x768",
      "800x600",
      "640x480"
    ])