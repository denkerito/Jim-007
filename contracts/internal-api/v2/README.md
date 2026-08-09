# Internal API contract v2

Version 2 removes Telegram-driven registration and introduces web-first,
two-phase Telegram linking. The snapshot covers every endpoint consumed by the
bot; `interactions.json` is validated by both services.

Regenerate the OpenAPI snapshot from `services/api` with:

```text
python scripts/internal_api_contract.py --write
```
