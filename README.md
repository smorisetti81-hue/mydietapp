# MyDietApp V88.4 — Calorie total fix

Fixes the Home calorie calculation so weekly-plan `eaten` flags from previous days/test sessions are no longer summed into today's total.

Today's total now considers only ingredient states belonging to today's planned meals, plus today's manual foods.

The temporary calorie diagnostic was aligned with the same day-scoped logic.


## V88.4 – Target calorie fisso
Il target alimentare mostrato e usato da Home non viene più ridotto dinamicamente in base al consumo Health Connect durante la giornata. Il dato Health Connect continua a essere disponibile come informazione sull'attività/consumo osservato, ma non modifica il target alimentare base del profilo.
