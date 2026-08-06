# Project Vision

voglio costruire un tracker per la palestra, in modo da tener tracciato:
- esercizi
- serie
- ripetizioni
- peso
- (note aggiuntive opzionale)

Al posto di inserire manualmente attraverso un interfaccia statica l'esercizio+serie+ripetizioni+peso in modo rigido, vorrei che l'utente potesse scrivere in un messaggio su telegram e un agente AI dovrebbe capire il messaggio per salvarlo su un database.

L'utente, inoltre, dovrebbe essere in grado di accedere ad una web app per visualizzare:
- workout history
- exercise history
- progressione nel tempo
- altre statistiche utili da definire

### Main Goals

- L'utente scrivendo in linguaggio naturale (anche non preciso) riesce a salvare il suo allenamento.
- Mantenere i dati strutturati dei workout.
- Mantenere basso accoppiamento tra i componenti.

## MVP

1. Telegram User registration
2. Workout creation through telegram
3. Add Exercise with multiple sets
4. Workout and Exercise History

## Functional Behavior

L'utente potrà:
- impostare una data differita in cui si salva il workout. Di default se non specificata la data di oggi.
- salvare pesi, ripetizioni, serie, note aggiuntive.
- recuperare direttamente dal bot in linguaggio naturale lo storico dei suoi esercizi (di default dal più recente, fino ad un tot).

Quando il bot riceve un messaggio, viene passato al backend, LLM non dovrà mai comunicare direttamente col db.

LLM dovrà restituire un JSON strutturato e standardizzato dal 
messaggio dell'utente.

L'output del LLM deve essere validato dall'applicazione prima di poter essere persistito nel database

Il logging di un workout usa una sessione applicativa persistita. L'utente apre un workout, aggiunge un esercizio con i relativi set per ogni messaggio e lo chiude con un comando esplicito. Il workout rimane `draft` fino alla chiusura e non contribuisce a storico o statistiche.
```
User:
ieri ho fatto panca 80x8 80x8 80x7
e lat machine 70x10x3
```

Il sistema può mantenere il contesto conversazionale minimo necessario per permettere all'utente di correggere l'ultima interpretazione. Al messaggio dell'utente il bot risponde scrivendo in modo altamente leggibile cosa ha interpretato, l'utente sempre in linguaggio naturale potrà rettificare in caso di errore.
```
User:
ieri panca 80x8 80x8 80x7

Bot:
Ho registrato:
Bench Press
80kg × 8
80kg × 8
80kg × 7

User:
no l'ultima erano 6 reps
```


## Architecture Principles

- LLM è responsabile solo di tradurre il linguaggio naturale in structured application data/intents.
- LLM non deve accedere al db direttamente.
- La business logic deve rimanere indipendente dalla piattaforma del bot (telegram/whatsapp) e dallo specifico provider del LLM.
- I componenti dovrebbero rimanere debolmente accoppiati.

## Stack

**Backend**
- FastAPI
- SQLAlchemy 2.0
- Alembic
- Pydantic v2

**Database**
- PostgreSQL

**AI**
- Gemini Developer API con `gemini-3.5-flash-lite`
- The application should not depend directly on a specific LLM provider

Il flusso Telegram dell'MVP e esplicito: `/workout [data naturale]` apre il draft,
ogni messaggio testuale aggiunge uno o piu esercizi e `/end` completa il workout.
Un'interpretazione ambigua non produce scritture e genera una richiesta di
chiarimento. Non e prevista conferma prima del salvataggio.

Gli storici sono disponibili con `/history [limite]` e
`/exercise <nome libero> [limite]`, con default 5 e massimo 20 risultati. Il primo
comando mostra soltanto workout completati. Il secondo risolve prima un nome esatto
nel catalogo personale e usa l'LLM solo per abbreviazioni o nomi colloquiali; una
risoluzione ambigua richiede chiarimento e non crea nuovi esercizi.

**Bot**
- python-telegram-bot

**Frontend**
- React + TypeScript
- TanStack Query
- Tailwind CSS

**Deployment**
- Docker Compose

## High-level architecture
Telegram
   │
   ▼
Telegram Bot
   │
   ▼
FastAPI
   │
   ▼
Application / Workout Service
   │
   ├──────────────► AI Service
   │                 │
   │            structured data
   │                 │
   │◄────────────────┘
   │
   ▼
Validation
   │
   ▼
Repository
   │
   ▼
PostgreSQL
