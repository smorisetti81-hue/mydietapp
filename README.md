# MyDietApp V95.1 — AI Provider Layer

V95.1 introduces a provider-neutral AI Engine.

## AI provider

Set `AI_PROVIDER` in Streamlit Secrets:

```toml
AI_PROVIDER = "gemini"
GEMINI_API_KEY = "..."
GEMINI_MODEL = "gemini-3.8-flash"
```

For OpenAI:

```toml
AI_PROVIDER = "openai"
OPENAI_API_KEY = "..."
OPENAI_MODEL = "gpt-5.6-luna"
```

The rest of the app uses one gateway function, so switching provider does not require changing the feature code.

`OPENAI_DEV_MODE = "true"` enables the existing developer telemetry panel.

## Development recommendation

Gemini 3.8 Flash currently has a Free Tier in the Gemini API, so it can be used for development without adding OpenAI API credit. OpenAI remains available as a separate provider for future testing/production decisions.
