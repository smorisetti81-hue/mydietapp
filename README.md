# MyDietApp V90 — Stabilizzazione funzionale

## V90
- Passaggio automatico alla settimana corrente quando esiste già una bozza della settimana arrivata.
- Calcolo calorie visualizzate di pasto reso coerente tra Home e Piano tramite una funzione comune.
- Dispensa: unità più adatte alla quantità reale (`g`, `kg`, `ml`, `l`, `pz`, `confezioni`).
- Dispensa: incrementi/decrementi coerenti con l'unità; i pezzi e le confezioni non avanzano più a multipli di 50.
- Acquisto dalla lista: incremento quantità dipendente dall'unità.
- Conversioni sicure `kg↔g` e `l↔ml`; restano attive solo le conversioni domestiche già note per alimenti come la banana.
- Nessuna modifica allo schema Supabase, Health Bridge o logica Smart Shopping live.

## V89 — Home UX redesign
- Home mobile-first redesign with softer graphite background.
- Compact calorie card and next-meal flow.
- Added deterministic Personal Trainer preview card (no new AI calls).
- Compact activity and shopping previews.
- Technical calorie diagnostics moved below the primary Home content.


## V90.1 – Preparazione settimana
- Promemoria guida: giovedì/venerdì è il momento consigliato per preparare la settimana successiva.
- Nel weekend viene mostrato un promemoria più urgente se la bozza non è ancora pronta.
- Nessuna scadenza bloccante: il piano può essere preparato in qualsiasi momento.
