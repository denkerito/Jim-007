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

I messaggi workout usano `POST /internal/workout-events`. Il bot traduce soltanto
`/workout`, testo libero e `/end` nelle azioni `open`, `log` e `complete`, allegando
una idempotency key derivata dal Telegram update ID. FastAPI risolve l'identita,
carica il contesto minimo e chiama Gemini tramite una porta applicativa
provider-neutral. L'adapter concreto usa Gemini Developer API e structured output;
non vengono abilitati tool o accessi al database.

La chiamata LLM avviene senza una transazione database aperta. Dopo la risposta,
FastAPI valida nuovamente il DTO, blocca il draft e persiste tutti gli esercizi del
messaggio in una singola transazione. Un errore su un esercizio annulla l'intero
messaggio.

Il comando Telegram `/start`, accettato solo in chat privata, registra o risolve
l'identita esterna tramite `POST /internal/identities/telegram`. FastAPI usa la
coppia stabile `(provider, provider_subject)` per collegarla a un utente applicativo
e aggiorna soltanto i metadati descrittivi Telegram nelle registrazioni successive.

Le letture Telegram usano `POST /internal/history-queries`, che risolve la stessa
identita provider-neutral e delega ai casi d'uso read-only. `/history` non usa il
provider LLM; `/exercise` evita la chiamata esterna per un nome normalizzato esatto
e usa Gemini soltanto per scegliere un ID gia presente nel catalogo personale. La
chiamata LLM avviene senza transazioni aperte e il risultato viene validato contro
il catalogo prima della query finale.

## Layer dell'API

Il backend separa esplicitamente quattro responsabilita:

- `domain`: entita e value object Pydantic immutabili, invarianti ed errori, senza dipendenze da FastAPI o SQLAlchemy;
- `application`: comandi, casi d'uso e porte per repository e Unit of Work;
- `infrastructure`: modelli ORM, mapper espliciti, repository SQLAlchemy e gestione della sessione;
- `api`: autenticazione interna, DTO HTTP, route e traduzione degli errori.

I modelli ORM non attraversano il confine dell'infrastruttura. Ogni caso d'uso apre una Unit of Work, condivide una sola `AsyncSession` tra i repository ed esegue un unico commit. I repository possono usare `flush`, ma non eseguono commit autonomamente.

La registrazione di un allenamento e incrementale: viene creato un workout `draft`, ogni messaggio aggiunge atomicamente un `WorkoutExercise` con tutti i suoi set e un comando esplicito porta infine il workout a `completed`. Non vengono mantenute transazioni database aperte tra messaggi o durante chiamate a servizi esterni.

Workout history ed exercise history sono proiezioni delle tabelle normalizzate e
includono solo workout `completed`. Usano paginazione keyset con un cursore opaco
basato su `(performed_on, created_at, id)` in ordine decrescente. L'exercise history
pagina per workout e raggruppa tutte le occorrenze dello stesso esercizio presenti
nello stesso allenamento.

Le API canoniche sono `GET /users/{user_id}/workouts` e
`GET /users/{user_id}/exercises/{exercise_id}/history`; entrambe accettano `limit`
(default 5, massimo 20) e un `cursor` opzionale e restituiscono `next_cursor` quando
esiste una pagina successiva.

## Persistenza e migrazioni

FastAPI usa SQLAlchemy in modalita asincrona con `asyncpg`. I modelli ORM appartengono all'infrastruttura e rimangono separati dai DTO HTTP, dal dominio e dagli output del provider LLM.

Le migrazioni sono gestite da Alembic attraverso il servizio one-shot `migrate`, costruito dalla stessa immagine dell'API. Dopo che PostgreSQL risulta sano, `migrate` esegue `alembic upgrade head`; FastAPI viene avviata soltanto se la migrazione termina con successo. Le migrazioni non vengono eseguite nello startup dell'API, evitando conflitti tra eventuali repliche in produzione.

FastAPI espone una liveness probe indipendente dal database e una readiness probe che verifica PostgreSQL con una query reale.
