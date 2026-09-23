# MyDietApp V93.5 — Inventario reale + Dispensa unificata

## V93.5
- L'inventario iniziale ora legge la **Dispensa reale** e mostra automaticamente gli alimenti già presenti.
- Gli alimenti già presenti vengono pre-selezionati quando si riapre l'inventario.
- Il catalogo personale viene alimentato dagli alimenti aggiunti manualmente e dagli alimenti già presenti in Dispensa.
- La ricerca dell'inventario considera sia il catalogo MyDiet sia il catalogo personale.
- Salvare nuovamente un alimento è **idempotente**: aggiorna la quantità invece di sommarla una seconda volta.
- Un alimento può avere contemporaneamente **confezioni + quantità sfusa**.
- Le confezioni con più formati già presenti vengono preservate quando si riapre e si salva l'inventario senza modificarne i lotti.
- La lista della spesa confronta le quantità usando lo stesso nome normalizzato della Dispensa, evitando falsi `0 g` dovuti a differenze di maiuscole/spaziatura.
- La Dispensa resta la **fonte di verità** per ciò che l'utente possiede.
- Il Piano resta indipendente dalla Dispensa: la disponibilità in casa serve a ottimizzare la settimana e calcolare cosa manca.

## V93.4
- Ricerca degli alimenti personalizzati già creati dall'utente.

## V93.3
- Ricerca e aggiunta manuale di qualsiasi alimento durante l'inventario iniziale.
- Quantità e confezioni salvate nella Dispensa reale.

## V91
- Gestione confezioni nella Dispensa.
