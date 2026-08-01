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

Il logging di un workout non richiede una sessione di allenamento stateful: un workout completo può essere descritto e registrato attraverso un singolo messaggio.
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
- Provider/model to be decided
- The application should not depend directly on a specific LLM provider

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
