# MyDietApp V94.1 — Prima settimana intelligente (OpenAI only)

Base: V93.5 Inventario + Dispensa unificata.

## AI
MyDiet usa esclusivamente l'OpenAI Responses API per le funzioni AI. Gemini è stato rimosso.

Secrets Streamlit richiesti:
- `OPENAI_API_KEY`
- opzionale `OPENAI_MODEL` (default: `gpt-5.6-luna`)

La generazione della prossima settimana usa la Dispensa come informazione di ottimizzazione, non come vincolo della dieta.
- Il piano resta guidato da profilo, obiettivo, stile, allergie/esclusioni e target calorico.
- Gli alimenti già presenti vengono privilegiati quando sensati e compatibili.
- Dopo la generazione viene mostrata una preview di copertura della Dispensa.
- La lista della spesa continua a calcolare il fabbisogno meno lo stock reale.
