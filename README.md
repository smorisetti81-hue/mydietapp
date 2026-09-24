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


## V95.5 — Gemini fallback esteso

Secrets Streamlit consigliati:
```toml
AI_PROVIDER = "gemini"
GEMINI_API_KEY = "..."
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_FALLBACK_MODELS = "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite"
```

Su 503/UNAVAILABLE il provider prova i modelli in ordine. La catena attraversa
anche modelli di fascia diversa, così un picco di capacità sui modelli Flash
principali non blocca necessariamente la generazione.

Nessun fallback su 429 (quota/rate limit), 401/403 (autenticazione/autorizzazione)
o altri errori di richiesta. `st.session_state["ai_fallback_log"]` conserva gli
switch della sessione; `ai_usage["last_call"]["model"]` mostra il modello
effettivamente utilizzato.

Il fallback esteso predefinito usa `gemini-3.5-flash` e `gemini-3.5-flash-lite`,
entrambi indicati dalla documentazione Google come modelli con Free Tier; i limiti
e la disponibilità effettivi dipendono dal progetto e dalle quote applicabili.
