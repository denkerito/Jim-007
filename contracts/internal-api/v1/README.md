# Internal API contract v1

`openapi.json` is the filtered provider contract used by the Telegram bot.
`interactions.json` contains consumer-owned examples exercised by both services.

After an intentional API change, review compatibility, update the interactions when
needed, and regenerate the snapshot from `services/api`:

```text
python scripts/internal_api_contract.py --write
```

Check the snapshot without modifying it with `--check`.
