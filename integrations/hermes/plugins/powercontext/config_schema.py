"""Declarative configuration for the PowerContext Hermes Provider."""

from plugins.memory.config_schema import (  # ty: ignore[unresolved-import]
    KIND_NUMBER,
    KIND_SECRET,
    KIND_TEXT,
    STORAGE_FLAT_JSON,
    ProviderConfigSchema,
    ProviderField,
)

CONFIG_SCHEMA = ProviderConfigSchema(
    name="powercontext",
    label="PowerContext",
    storage=STORAGE_FLAT_JSON,
    docs_url="https://github.com/oceanbase/powercontext/tree/master/integrations/hermes",
    fields=(
        ProviderField(
            key="base_url",
            label="PowerContext server URL",
            kind=KIND_TEXT,
            default="http://127.0.0.1:8000",
            description="Base URL of the running PowerContext server.",
            inline=True,
        ),
        ProviderField(
            key="authorization",
            label="Authorization header",
            kind=KIND_SECRET,
            env_key="POWERCONTEXT_HERMES_AUTHORIZATION",
            description="Optional complete header value, for example: Bearer <token>.",
            inline=True,
        ),
        ProviderField(
            key="scope_id",
            label="Memory scope template",
            kind=KIND_TEXT,
            default="hermes:{profile}:{user_id}",
            description="Supports {profile}, {agent_identity}, {user_id}, and {hermes_home}.",
        ),
        ProviderField(
            key="max_bytes",
            label="Maximum recalled context bytes",
            kind=KIND_NUMBER,
            default="8000",
            description="Bounded context returned by /v1/context/prepare.",
        ),
        ProviderField(
            key="timeout",
            label="HTTP timeout in seconds",
            kind=KIND_NUMBER,
            default="5",
            description="Per-request timeout for PowerContext.",
        ),
    ),
)
