# MyDietApp V91 — Gestione confezioni

## V91
- Aggiunta modalità **Confezione** nella Dispensa.
- Un prodotto confezionato può essere registrato come `N confezioni × contenuto per confezione`.
- MyDiet calcola automaticamente il totale disponibile.
- Esempi supportati: `2 × 500 g = 1 kg`, `2 × 1 l = 2 l`, `6 × 1 pz = 6 pz`.
- Il formato della confezione viene conservato anche quando il consumo riduce la quantità disponibile.
- Le quantità libere già esistenti restano compatibili.
- Nessuna modifica a Supabase, Health Bridge, Smart Shopping o al Piano.

## V90.3
- Non mostra più il menu della settimana corrente dentro "Prossima settimana" quando la nuova settimana non è ancora stata generata.
- La nuova settimana mostra uno stato vuoto esplicito e invita a generarla.
- Mantiene la guida alla preparazione anticipata, idealmente giovedì/venerdì, per organizzare pasti e spesa.
