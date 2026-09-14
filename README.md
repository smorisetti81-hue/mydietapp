# MyDietApp V86.6 — PostgreSQL meal order fix

Fixes the remaining display inconsistency in the Piano page: meals are always rendered in the canonical order **Colazione → Spuntino → Pranzo → Cena**, independently of PostgreSQL JSONB object-key order.

No database migration is required and no persistence logic is changed.

Deploy by replacing the project files, then:

```powershell
git add .
git commit -m "MyDietApp V86.6 - Fix meal display order"
git push
```
