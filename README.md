# SchemaForge — AI Schema Generation Platform [Live Link: SchemaForge](https://schemaforge-ai.vercel.app) | [GitHub: SchemaForge AI backend](https://github.com/Lithinpavansai/AI_Builder_Backend)

FastAPI · Next.js · Groq API · Pydantic v2 · Render · Vercel

• Built a single-pass LLM pipeline (Intent Extraction → System Design → Schema Generation → Refinement) that converts natural language into UI, API, DB, and Auth schemas in one JSON output, consolidating 5 LLM calls into 1.
• Built a 0–100 runtime validator (6 structural checks); tested on 23 prompts incl. adversarial cases: 100% completion, 84/100 average. A manual audit of 3 outputs caught a real cross-layer bug (Admin-only rule vs. config allowing Customer).

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
| LLM Provider | Groq (llama-3.3-70b-versatile) |
| Validation | Pydantic v2 |
| Async Jobs | Python asyncio |
| Deployment | Render |

## 📊 Evaluation

23 test prompts: 20 run locally (10 normal + 10 edge cases) and 3 run on the live deployment.
- Normal: CRM, E-commerce, LMS, Healthcare, HR, Social Media
- Edge cases: Vague, Single word, Conflicting, Incomplete, Gibberish, Non-English

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
# Add GROQ_API_KEY to .env
python -m uvicorn app.main:app --reload
```
