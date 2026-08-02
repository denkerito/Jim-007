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

## Persistenza e migrazioni

FastAPI usa SQLAlchemy in modalita asincrona con `asyncpg`. I modelli ORM appartengono all'infrastruttura e rimangono separati dai DTO HTTP, dal dominio e dagli output del provider LLM.

Le migrazioni sono gestite da Alembic attraverso il servizio one-shot `migrate`, costruito dalla stessa immagine dell'API. Dopo che PostgreSQL risulta sano, `migrate` esegue `alembic upgrade head`; FastAPI viene avviata soltanto se la migrazione termina con successo. Le migrazioni non vengono eseguite nello startup dell'API, evitando conflitti tra eventuali repliche in produzione.

FastAPI espone una liveness probe indipendente dal database e una readiness probe che verifica PostgreSQL con una query reale.

