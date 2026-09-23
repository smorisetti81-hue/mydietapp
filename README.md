# MyDietApp V94.1 — Prima settimana intelligente (OpenAI only)

Base: V93.5 Inventario + Dispensa unificata.

## AI
MyDiet usa esclusivamente l'OpenAI Responses API per le funzioni AI. V95 introduce un unico AI Engine con modello configurabile, telemetry DEV, stima costi e cache delle risposte.

Secrets Streamlit richiesti:
- `OPENAI_API_KEY`
- opzionale `OPENAI_MODEL` (default: `gpt-5.6-luna`)

La generazione della prossima settimana usa la Dispensa come informazione di ottimizzazione, non come vincolo della dieta.
- Il piano resta guidato da profilo, obiettivo, stile, allergie/esclusioni e target calorico.
- Gli alimenti già presenti vengono privilegiati quando sensati e compatibili.
- Dopo la generazione viene mostrata una preview di copertura della Dispensa.
- La lista della spesa continua a calcolare il fabbisogno meno lo stock reale.


## V95 · AI Engine

Secrets opzionali:

```toml
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-5.6-luna"
OPENAI_DEV_MODE = "true"
```

`OPENAI_DEV_MODE=true` mostra solo in sviluppo un pannello con chiamate, token, latenza e costo stimato della sessione. Non è necessario per l'utente finale.

L'app passa tutte le richieste AI attraverso `openai_interaction()`. La generazione della settimana usa reasoning `low`; le funzioni AI leggere usano `low` o `minimal`. Il consiglio del prossimo pasto è cacheato per contesto nella sessione.
