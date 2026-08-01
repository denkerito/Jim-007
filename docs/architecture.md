# Architecture

## Struttura dei servizi

Il progetto usa un monorepo con servizi separati e immagini Docker indipendenti:

- `telegram-bot`: adapter sottile verso Telegram;
- `api`: backend FastAPI e unica autorita sulla business logic;
- `db`: database PostgreSQL.

Il flusso applicativo previsto e:

```text
Telegram -> Telegram Bot -> HTTP -> FastAPI -> PostgreSQL
                                      |
                                      +-> API del provider LLM
```

Il bot non accede direttamente al database e non contiene logica di dominio. Comunica con FastAPI attraverso un contratto HTTP e un token interno. FastAPI e inoltre il backend della futura web application.


