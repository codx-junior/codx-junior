from pydantic import BaseModel, Field
from enum import Enum
from typing import Union, Optional, List

class AIProvider(BaseModel):
    name: Optional[str] = Field(default="", description="Provider name") 
    provider: Optional[str] = Field(default="", description="OpenAI-compatible provider protocol")
    api_url: Optional[str] = Field(description="Optional url if provider is remote", default="")
    api_key: Optional[str] = Field(description="Optional api key", default="")
    admin_url: Optional[str] = Field(description="Optional url if provider has an admin url", default="")
    pricing_url: Optional[str] = Field(description="Optional url to the provider's pricing page", default="")
    input_k_tokens_cxjcoins: Optional[float] = Field(
        description="Cost in cxjcoins per 1K tokens at provider level. Applies to all models unless overridden at model level.",
        default=None
    )
    output_k_tokens_cxjcoins: Optional[float] = Field(
        description="Cost in cxjcoins per 1K tokens at provider level. Applies to all models unless overridden at model level.",
        default=None
    )
    price_list: Optional[List["AIModelPrice"]] = Field(
        description="List of model prices fetched from the provider's pricing page.",
        default=[]
    )
    tool_timeout_seconds: Optional[float] = Field(
        default=None,
        description=(
            "Maximum seconds a single tool execution may run at provider level "
            "before it is aborted and reported to the model as a tool error."
        )
    )
    reasoning_effort: Optional[str] = Field(
        default=None,
        description=(
            "Provider-level reasoning effort (off/low/high/max). "
            "Model-level takes precedence. 'off' disables thinking on "
            "providers that support it (DeepSeek)."
        )
    )
    thinking: Optional[str] = Field(
        default=None,
        description=(
            "Provider-level thinking mode (enabled/disabled). "
            "Model-level takes precedence."
        )
    )

class AIModelPrice(BaseModel):
    model_name: str = Field(description="Model name as listed by the provider")
    input_price_per_1k_tokens: float = Field(description="Cost per 1K input tokens in USD")
    output_price_per_1k_tokens: float = Field(description="Cost per 1K output tokens in USD")

# Rebuild AIProvider so that the forward reference to AIModelPrice is resolved
AIProvider.model_rebuild()

class AILLMModelSettings(BaseModel):
    temperature: Optional[float] = Field(default=1, description="Model temperature")
    context_length: Optional[int] = Field(default=0)
    merge_messages: Optional[bool] = Field(description="Flat conversation into a single message before sending to model", default=False)
    reasoning_effort: Optional[str] = Field(
        default=None,
        description=(
            "Reasoning effort for thinking models: off/low/high/max. "
            "Delegated to the provider; 'off' disables DeepSeek thinking. "
            "When unset the provider default is used."
        )
    )
    thinking: Optional[str] = Field(
        default=None,
        description=(
            "Explicit thinking mode: enabled/disabled (DeepSeek). "
            "When unset the provider default is used."
        )
    )
    
class AIEmbeddingModelSettings(BaseModel):
    vector_size: Optional[int] = Field(default=1536, description="Model vector size")
    chunk_size: Optional[int] = Field(default=8190, description="Model chunk_size")

class AIModelType(str, Enum):
    llm = 'llm'
    embeddings = 'embeddings'
    image = 'image'

class AIModel(BaseModel):
    name: str = Field(description="Model name")    
    model_type: AIModelType = Field(description="Model type", default=AIModelType.llm)
    ai_provider: str = Field(description="AI Provider name")
    ai_model: Optional[str] = Field(description="AI Provider's model name", default=None)
    settings: Union[AILLMModelSettings, AIEmbeddingModelSettings] = Field(description="Model settings")
    metadata: Optional[dict] = Field(description="Model's last update date", default={})
    url: Optional[str] = Field(description="Model info", default="")
    system: Optional[str] = Field(description="Model system instructions", default="")
    prompt_template: Optional[str] = Field(description="Model info", default="{ MESSAGE }")
    tags: List[str] = Field(description="Model tags", default=[])
    model_file: Optional[str] = Field(description="Custom Modelfile", default=None)
    tool_timeout_seconds: Optional[float] = Field(
        default=None,
        description=(
            "Maximum seconds a single tool execution may run at model level "
            "(overrides provider)."
        )
    )

class AISettings(BaseModel):
    provider: Optional[str] = Field(default="")
    provider_type: Optional[str] = Field(default="") 
    api_url: Optional[str] = Field(default="")
    api_key: Optional[str] = Field(default="")
    model: Optional[str] = Field(default="")
    system: Optional[str] = Field(description="Model system instructions", default="")
    prompt_template: Optional[str] = Field(description="Model info", default="")
    context_length: Optional[int] = Field(default=0)
    temperature: Optional[float] = Field(default=0.8)
    vector_size: Optional[int] = Field(default=1536)
    chunk_size: Optional[int] = Field(default=8190)
    merge_messages: Optional[bool] = Field(default=False)
    model_type: AIModelType = Field(description="Model type", default=AIModelType.llm)
    url: Optional[str] = Field(description="Model info", default="")
    input_k_tokens_cxjcoins: Optional[float] = Field(
        description="Resolved cost in cxjcoins per 1K tokens (model-level takes precedence over provider-level).",
        default=None
    )
    output_k_tokens_cxjcoins: Optional[float] = Field(
        description="Resolved cost in cxjcoins per 1K tokens (model-level takes precedence over provider-level).",
        default=None
    )
    tool_timeout_seconds: Optional[float] = Field(
        default=None,
        description=(
            "Resolved maximum seconds a single tool execution may run "
            "(model-level takes precedence over provider-level). ``None`` "
            "means the runtime default/environment value is used."
        )
    )
    tool_parallelism: Optional[int] = Field(
        default=None,
        description=(
            "Resolved maximum number of tool calls of one model round that may "
            "run concurrently (model-level takes precedence over "
            "provider-level). ``None`` means the runtime "
            "CODX_JUNIOR_TOOL_PARALLELISM/default value is used; ``1`` disables "
            "tool parallelism."
        )
    )
    reasoning_effort: Optional[str] = Field(
        default=None,
        description=(
            "Resolved reasoning effort (model > provider > environment). "
            "'off' disables thinking on providers that support it."
        )
    )
    thinking: Optional[str] = Field(
        default=None,
        description="Resolved explicit thinking mode (enabled/disabled)."
    )


# Made with ❤️ by codx-junior