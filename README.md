# SchemaForge — AI Schema Generation Platform

[Live app](https://schemaforge-ai.vercel.app) | [Frontend repo](https://github.com/Lithinpavansai/AI_Builder_frontend)

SchemaForge turns a plain-English app description into validated UI, API, DB, and Auth schemas using a 4-stage LLM pipeline.

FastAPI · Groq API · Pydantic v2 · Render

## 🏗️ Architecture
User Prompt
↓
Stage 1: Intent Extraction      → Parses entities, roles, features
↓
Stage 2: System Design          → Architecture, pages, API groups, DB tables
↓
Stage 3: Schema Generation      → UI + API + DB + Auth + Business Logic (5 parallel Groq calls)
↓
Stage 4: Refinement             → Cross-layer consistency repair
↓
Validation + Repair Engine      → Pydantic validation + auto-fix
↓
Runtime Validator               → Proves schema is executable

## 🚀 Live API

Base URL: `https://app-compiler-api.onrender.com`

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Health check |
| `/api/generate` | POST | Full pipeline (sync) |
| `/api/generate/async` | POST | Full pipeline (async, returns job_id) |
| `/api/status/{job_id}` | GET | Poll job progress |
| `/api/result/{job_id}` | GET | Fetch completed result |
| `/api/runtime-validate` | POST | Runtime executability check |
| `/api/eval/dataset` | GET | View test dataset |
| `/api/eval/run-single/{id}` | POST | Run single eval prompt |
| `/docs` | GET | Swagger UI |

## 🧩 Pipeline Stages

### Stage 1 — Intent Extraction
Parses raw user prompt into structured `IntentOutput` with entities, roles, features, and assumptions.

### Stage 2 — System Design  
Converts intent into app architecture — pages, API groups, DB tables, auth flow, business rules.

### Stage 3 — Schema Generation
5 focused Groq calls generate:
- UI Schema (pages, components, layouts)
- API Schema (endpoints, methods, auth, validation)
- DB Schema (tables, columns, relations)
- Auth Schema (roles, permissions, JWT config)
- Business Logic (rules, conditions, affected routes)

### Stage 4 — Refinement
Detects and fixes cross-layer inconsistencies:
- Roles used in UI/API but undefined in Auth
- DB relations pointing to non-existent tables
- Business rule routes with no matching API endpoint

### Validation + Repair Engine
- Pydantic v2 models enforce schema contracts
- Auto-repair triggers on validation failure
- Max 3 repair attempts per stage
- Graceful degradation on exhaustion

### Runtime Validator
Proves output is executable with 6 checks:
- Route completeness
- Auth coverage
- DB coverage
- Role consistency
- Foreign key validity
- Business rule route validity

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI |
| LLM Provider | Groq (`openai/gpt-oss-120b`) |
| Validation | Pydantic v2 |
| Async Jobs | Python asyncio |
| Deployment | Render |

> [!NOTE]
> **Model Lifecycle Notice**: Groq periodically deprecates, retires, and renames models. The active LLM model ID is configured via the `GROQ_MODEL` environment variable (pinned to `openai/gpt-oss-120b`). All benchmark evaluation metrics and validator scores are directly tied to the model version named.

## 📊 Evaluation

23 test prompts: 20 run locally (10 normal + 10 edge cases) and 3 run on the live deployment.
- Normal: CRM, E-commerce, LMS, Healthcare, HR, Social Media
- Edge cases: Vague, Single word, Conflicting, Incomplete, Gibberish, Non-English
- Live deployment: Storefront catalog, prompt injection resistance, and zero-user login handling

Run evaluation runner:
```bash
python -m app.evaluation.runner
```

Run single eval:
```bash
POST /api/eval/run-single/N01
```

## 🏃 Local Setup

```bash
git clone https://github.com/Lithinpavansai/AI_Builder_Backend.git
cd AI_Builder_Backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Set GROQ_API_KEY in .env (GROQ_MODEL defaults to openai/gpt-oss-120b)
python -m uvicorn app.main:app --reload
```
