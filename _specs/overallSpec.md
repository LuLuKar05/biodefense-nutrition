

## Overview
A full-stack platform combining personalized nutrition, bioinformatics, and decentralized AI that dynamically adjusts dietary recommendations based on local health threats (like viral outbreaks). The system detects threats, simulates viral structures, screens natural food compounds for neutralization, and pushes pathogen-resistant meal plans to users.

**All user interaction happens through chat bots** (Telegram + Discord for MVP). A separate Next.js web dashboard serves as the **system visualization layer** for admins and hackathon judges to observe the full pipeline in real time.

**Tech Stack:**
- **User Interface:** Telegram Bot + Discord Bot (powered by Z.ai agents)
- **System Dashboard:** Next.js (React) — admin/judge visualization layer
- **Backend API:** Python (FastAPI) — core logic + bot webhook handlers
- **AI Agent Framework:** Z.ai — conversational onboarding, meal planning, alerts
- **Database:** MongoDB
- **Background Workers:** Celery + Redis
- **Compute:** Amina CLI (cloud cluster) for ESMFold & DiffDock
- **Federated Learning:** FLock Alliance
- **Containerization:** Docker Compose

**MVP Strategy:** All 5 phases live with graceful fallback to pre-computed mocks at Phases 3-4. This ensures the demo always works while showing the real pipeline when it succeeds.

**Bot MVP Scope:** Telegram + Discord for MVP. WhatsApp is a stretch goal (requires Meta Business API approval).

---

## Architecture

```
┌──────────────┐  ┌──────────────┐
│ Telegram Bot │  │ Discord Bot  │   ◄── User-facing (chat)
└──────┬───────┘  └──────┬───────┘
       │                 │
       ▼                 ▼
┌────────────────────────────────┐
│     FastAPI Backend            │
│  (Webhook handlers + API)      │
└──────────────┬─────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
┌─────────────┐  ┌─────────────┐
│  MongoDB    │  │ Celery      │
│             │  │ Workers     │
└─────────────┘  │ (Redis)     │
                 └──────┬──────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌──────────┐   ┌──────────┐   ┌──────────────┐
  │ AQI API  │   │  NCBI    │   │  Amina CLI   │
  │ ProMED   │   │ GenBank  │   │ (ESMFold /   │
  └──────────┘   └──────────┘   │  DiffDock)   │
                                └──────────────┘
               │
       ┌───────┴───────┐
       ▼               
┌─────────────────┐
│  Next.js        │  ◄── System Dashboard (admin/judges)
│  Dashboard      │      Real-time pipeline visualization
└─────────────────┘
```

---

## Project Structure

```
OpenClawHack/
├── _specs/
│   └── overallSpec.md              # This file
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI entry point, CORS, webhook routes
│   │   ├── routers/
│   │   │   ├── users.py            # User profile & meal plan API endpoints
│   │   │   ├── threats.py          # Threat detection & docking result endpoints
│   │   │   └── webhooks.py         # Telegram & Discord webhook handlers
│   │   ├── bots/
│   │   │   ├── telegram_bot.py     # Telegram bot logic (Z.ai agent integration)
│   │   │   ├── discord_bot.py      # Discord bot logic (Z.ai agent integration)
│   │   │   └── conversation.py     # Shared conversation flow (onboarding, meal log, alerts)
│   │   └── services/
│   │       ├── nutrition.py        # TDEE / macro calculation
│   │       ├── meal_planner.py     # Z.ai agent meal generation
│   │       ├── adaptive_planner.py # Phase 5 adaptive meal rewrite
│   │       ├── threat_detection.py # AQI + public health querying
│   │       ├── genbank.py          # NCBI GenBank sequence fetching
│   │       └── flock_federated.py  # FLock Alliance integration
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── worker/
│   └── tasks/
│       ├── scan_threats.py         # Celery periodic task (every 6h)
│       ├── fold_protein.py         # Amina CLI ESMFold task
│       └── dock_ligands.py         # Amina CLI DiffDock task
├── dashboard/                      # Next.js — System Dashboard (admin/judges)
│   ├── src/
│   │   └── app/
│   │       ├── page.tsx            # Overview: active users, zones, pipeline status
│   │       ├── pipeline/           # Real-time pipeline visualization
│   │       ├── threats/            # Threat map & docking results viewer
│   │       └── users/              # Per-user meal plan & adaptation log
│   └── Dockerfile
├── data/
│   ├── phytochemicals.json         # Curated 20-30 phytochemical-to-food mappings
│   ├── meal_templates.json         # Fallback meal plan templates
│   ├── mock_sequences/             # Pre-downloaded FASTA files
│   ├── mock_pdb/                   # Pre-computed .pdb structures
│   └── mock_docking/               # Pre-computed docking result JSONs
├── docker-compose.yml              # MongoDB + Redis + backend + worker + dashboard
├── .env.example
├── .gitignore
└── instruction.md
```

---

## Phase 1: Get User Data & Target Body Goal (The Nutritionist Agent)

### Concept
Collects the user's health condition, preferred food types, target body goals, daily calorie burn, and previous nutritional intake. Calculates exact macronutrient needs and generates a highly personalized daily meal plan.

### Inputs
- Allergies
- Preferred diet type (vegan, keto, Mediterranean, etc.)
- Current weight (kg)
- Height (cm)
- Daily calorie burn
- Location (city/zip)
- Body goal (cut / bulk / maintain)

### Implementation

**Backend — `backend/app/routers/users.py`**
- `POST /api/users/onboard` — Accepts user profile JSON, stores in MongoDB `users` collection.

**Backend — `backend/app/services/nutrition.py`**
- Calculate TDEE using Mifflin-St Jeor equation:
  - Male: `10 × weight(kg) + 6.25 × height(cm) − 5 × age − 161`
  - Female: `10 × weight(kg) + 6.25 × height(cm) − 5 × age + 5`
- Compute macro split (protein/carbs/fat) based on body goal:
  - **Cut:** 40% protein / 30% carbs / 30% fat
  - **Bulk:** 30% protein / 45% carbs / 25% fat
  - **Maintain:** 30% protein / 40% carbs / 30% fat

**Backend — `backend/app/services/meal_planner.py`**
- Use the Z.ai agent to generate a daily meal plan matching computed macros, respecting allergies and diet type.
- **Fallback:** Template-based meal plan generator using predefined meals in `data/meal_templates.json`.

**Chat Bots — `backend/app/bots/conversation.py` (Onboarding Flow)**
All user interaction happens through **Telegram and Discord bots** (powered by Z.ai agents). The conversational onboarding flow:
1. Bot greets user → asks for **name, age, gender**.
2. Bot asks for **allergies** (free text or pick from common list).
3. Bot asks for **diet type** (vegan, keto, Mediterranean, etc. — button/inline keyboard).
4. Bot asks for **current weight (kg)** and **height (cm)**.
5. Bot asks for **daily activity level** (sedentary, light, moderate, active — used for calorie burn estimate).
6. Bot asks for **body goal** (cut / bulk / maintain — button selection).
7. Bot asks for **location** (city name or zip code).
8. Bot confirms the profile summary → user confirms → data stored via `POST /api/users/onboard`.
9. Bot generates and sends the **daily meal plan** as a formatted message (with macros, calories, meal breakdown).

**Bot platforms:**
- `backend/app/bots/telegram_bot.py` — Telegram Bot API (python-telegram-bot or aiogram) + Z.ai agent.
- `backend/app/bots/discord_bot.py` — Discord Bot (discord.py) + Z.ai agent.
- Both share `conversation.py` for the unified onboarding/meal plan/alert logic.

**Daily Interaction (Post-Onboarding):**
- User can type what they ate → bot logs it (e.g., "I ate grilled chicken and rice").
- User can ask for today's meal plan → bot returns it.
- User can update goals or weight → bot triggers recalculation.

**System Dashboard — `dashboard/` (Next.js)**
- NOT user-facing. For admins and hackathon judges.
- Shows real-time pipeline status, active users per zone, threat alerts, meal plan adaptations.
- Visualizes the full Phase 1→5 data flow so judges can see what's happening behind the chat.

### MVP Scope
- **Telegram + Discord** bots for MVP. WhatsApp is a stretch goal (requires Meta Business API approval).
- User input via chat only (no wearable APIs or food image recognition).
- *Stretch:* Integrate Nutritionix Natural Language API so users can log meals naturally in chat (e.g., "I ate an apple and a turkey sandwich" → auto-parsed macros).

---

## Phase 2: Threat Detection & Target Acquisition (The Biodefence Radar)

### Concept
Actively monitors the user's location against environmental APIs (AQI) and public health databases. If it detects a localized outbreak (e.g., H5N1), it pulls the genetic blueprint of the threat from NCBI GenBank.

### Inputs
- User location (from Phase 1 profile)

### Outputs
- Raw Amino Acid Sequence (1D text data) of the circulating virus's target protein (e.g., H5N1 Hemagglutinin spike).

### Implementation

**Backend — `backend/app/services/threat_detection.py`**
- Query AQI API (IQAir or OpenWeatherMap Air Pollution API) using user's location coordinates.
- Query public health data source (WHO Disease Outbreak News RSS feed or ProMED) for outbreak alerts matching the user's region.

**Backend — `backend/app/services/genbank.py`**
- When a threat is detected (e.g., "H5N1" in the user's region), use Biopython's `Entrez.esearch` + `Entrez.efetch` to pull the latest amino acid sequence from NCBI GenBank.
- Parse the FASTA response and extract the amino acid sequence string.

**Worker — `worker/tasks/scan_threats.py`**
- Celery periodic task running every 6 hours per tracked geographic zone.
- Store results in MongoDB `threats` collection: `{ zone, threat_name, amino_acid_seq, detected_at }`.

**Fallback Mock**
- Pre-downloaded sequences stored in `data/mock_sequences/` (e.g., `h5n1_hemagglutinin.fasta`, `sars_cov2_spike.fasta`).
- If live API fails or times out, serve these.

### Usage
- Z.ai Agent + NCBI GenBank API + Public Health/AQI APIs.

---

## Phase 3: Structure Prediction (Taking the 3D Mugshot)

### Concept
Automatically pipes the amino acid sequence into a computational biology engine (ESMFold via Amina CLI) to simulate its physics and predict the exact 3D shape of the viral protein.

### Inputs
- Amino Acid Sequence from Phase 2.

### Outputs
- A `.pdb` file containing the 3D atomic structure of the viral protein spike.

### Implementation

**Worker — `worker/tasks/fold_protein.py`**
- Accept an amino acid sequence from the `threats` collection.
- Call the Amina CLI `esmfold` command (cloud API endpoint) with the sequence.
- Store the returned `.pdb` file in MongoDB GridFS or local `data/pdb_cache/`, keyed by `threat_name + sequence_hash`.

**API Endpoint**
- `GET /api/threats/{threat_id}/structure` — Returns the PDB file or a status (`"computing"`, `"ready"`, `"failed_using_fallback"`).

**Fallback Mock**
- Pre-computed `.pdb` files for 2-3 known viruses (H5N1, SARS-CoV-2) stored in `data/mock_pdb/`.
- If Amina CLI call fails or credits are exhausted, serve cached structures.

### Architecture Note
- Runs at the **system/backend layer**, NOT the app layer.
- Offloaded to Amina CLI cloud cluster (using hackathon credits).
- Background worker (Celery) runs these based on geographic zones to minimize app latency and redundant compute.

---

## Phase 4: Phytochemical Library Screening (Molecular Docking)

### Concept
Acts as a virtual lab. Takes a curated library of natural food compounds (phytochemicals) and simulates throwing them at the 3D virus structure to see which ones bind to and neutralize the virus. Uses the `--background` flag for concurrent simulation.

### Inputs
- 3D Virus Structure (`.pdb` from Phase 3)
- SMILES strings of known phytochemicals

### Outputs
- JSON: `[{ threat_name, top_ligand, confidence_score }]`

### Implementation

**Static Data — `data/phytochemicals.json`**
A curated library of ~20-30 phytochemicals (sourced from FooDB — foodb.ca):

| Phytochemical | SMILES (truncated) | Food Sources |
|---|---|---|
| Quercetin | `O=C1C(O)=C(...)` | Red Onions, Apples, Berries |
| EGCG | `OC1=CC(...)` | Green Tea |
| Curcumin | `COC1=CC(...)` | Turmeric |
| Allicin | `C=CCS(=O)SCC=C` | Garlic |
| Resveratrol | `OC1=CC(...)` | Red Grapes, Peanuts |
| Sulforaphane | `CS(=O)CCCCN=C=S` | Broccoli, Brussels Sprouts |
| Gingerol | `CCCCC(O)CC(=O)...` | Ginger |
| Lycopene | ... | Tomatoes |
| Capsaicin | ... | Chili Peppers |
| Luteolin | ... | Celery, Parsley |
| ... | ... | ... |

**Worker — `worker/tasks/dock_ligands.py`**
- For each phytochemical in the library, call Amina CLI `diffdock` with the `.pdb` structure and the SMILES string (using `--background` for concurrency).
- Collect results and rank by binding affinity / confidence score.
- Store in MongoDB `docking_results` collection: `{ threat_id, ligand_name, smiles, confidence_score, food_sources }`.

**API Endpoint**
- `GET /api/threats/{threat_id}/docking-results` — Returns top-N ligands with confidence scores and their food sources.

**Fallback Mock**
- Pre-computed docking results in `data/mock_docking/h5n1_results.json`.
- If Amina CLI fails, serve these.

### Data Mapping (Phase 4 → 5 Bridge)
- The `data/phytochemicals.json` file serves as the chemical-to-food lookup.
- Each entry maps `{ compound_name, smiles }` → `{ food_sources: [{ food, serving_info }] }`.
- This eliminates the need to query the full FooDB database at runtime.

---

## Phase 5: App Layer Integration & Alert (The Defense Protocol)

### Concept
The Nutritionist Agent receives molecular docking results, cross-references the winning ligand with everyday foods, and dynamically rewrites the user's meal plan to heavily feature these foods — while still aligning with the user's original Phase 1 weight/fitness goals.

### Inputs
- JSON output from Phase 4 (top ligands + food sources)
- Phase 1 user baseline data (macros, allergies, diet type)

### Outputs
- Real-time **chat push alert** to affected users (Telegram / Discord message)
- Dynamically adjusted, pathogen-resistant meal plan sent via bot

### Implementation

**Backend — `backend/app/services/adaptive_planner.py`**
- Take the top docking results (e.g., Quercetin with 92% confidence).
- Look up their `food_sources` from `data/phytochemicals.json`.
- Re-run the Z.ai meal planner agent with an additional constraint: "heavily feature Red Onions, Green Tea, Garlic" while still meeting Phase 1 macro targets and allergy restrictions.
- Store the adapted plan in MongoDB `meal_plans` collection: `{ user_id, date, original_plan, adapted_plan, threat_context }`.

**Bot Alert Push — `backend/app/bots/conversation.py`**
- When a new threat is detected in a user's zone, the bot **proactively messages** the user:
  - "⚠️ **H5N1 Alert** detected in your area."
  - "Your meal plan has been optimized to include foods with natural antiviral compounds."
  - Shows the adapted meal plan with highlighted **biodefense foods** (e.g., "🧅 Red Onions — Quercetin, 92% binding confidence").
- For Telegram: use `bot.send_message(chat_id, ...)` to push alerts.
- For Discord: use DM or channel message via `discord.py`.
- Users can reply to ask questions about why foods changed → Z.ai agent explains.

**System Dashboard — `/dashboard` (Judges View)**
- Show a **threat alert banner** on the admin dashboard with full pipeline details.
- Display adapted meal plans with highlighted "biodefense foods" and their associated phytochemical + confidence score.
- Visual diff between original and adapted plan.
- Real-time map showing which zones have active threats and how many users were notified.

**FLock Alliance — Federated Learning (Stretch Goal)**
`backend/app/services/flock_federated.py`:
- Collect anonymized efficacy signals: user-reported symptom frequency, adherence to adapted meals.
- `POST /api/users/{id}/report-symptoms` — Endpoint for user self-reporting.
- Train a lightweight local model (logistic regression or small NN) mapping `{ phytochemical_consumed, region, outcome }`.
- Push model weights (NOT raw data) to FLock Alliance for federated aggregation.
- Pull aggregated weights back to refine `confidence_score` adjustments in future docking result rankings.
- **For hackathon demo:** Simulate with a before/after visualization showing how federated weight updates shift the confidence rankings.

---

## API Endpoints Summary

| Method | Endpoint | Phase | Description |
|--------|----------|-------|-------------|
| `POST` | `/api/users/onboard` | 1 | Create user profile with health data |
| `GET` | `/api/users/{id}/meal-plan` | 1 / 5 | Get current (possibly adapted) meal plan |
| `POST` | `/api/users/{id}/log-meal` | 1 | Log a meal (from bot chat input) |
| `GET` | `/api/threats?zone={zone}` | 2 | List active threats for a geographic zone |
| `GET` | `/api/threats/{id}/structure` | 3 | Get PDB structure file or status |
| `GET` | `/api/threats/{id}/docking-results` | 4 | Get top-N ligand results with food sources |
| `POST` | `/api/users/{id}/report-symptoms` | 5 | Submit symptom self-report for FLock |
| `POST` | `/webhook/telegram` | Bot | Telegram bot webhook endpoint |
| `POST` | `/webhook/discord` | Bot | Discord bot interactions endpoint |
| `GET` | `/api/dashboard/pipeline-status` | Dashboard | Real-time pipeline status for judge view |
| `GET` | `/api/dashboard/zone-summary` | Dashboard | Active threats & user counts per zone |

---

## Background Worker Schedule (Celery Beat)

| Task | Frequency | Description |
|------|-----------|-------------|
| `scan_threats` | Every 6 hours | AQI + public health check per tracked zone |
| `fold_protein` | On new threat detection | ESMFold via Amina CLI |
| `dock_ligands` | After fold completes | DiffDock for all phytochemicals vs. new structure |

---

## Environment Variables (`.env.example`)

```
MONGODB_URI=mongodb://localhost:27017/biodefense
REDIS_URL=redis://localhost:6379/0

# Bot Tokens
TELEGRAM_BOT_TOKEN=
DISCORD_BOT_TOKEN=
DISCORD_APP_ID=

# External APIs
NCBI_API_KEY=
AQI_API_KEY=
ZAI_API_KEY=
AMINA_API_KEY=
NUTRITIONIX_APP_ID=
NUTRITIONIX_APP_KEY=
FLOCK_ALLIANCE_KEY=

# Webhook (for production deployment)
WEBHOOK_BASE_URL=https://your-domain.com
```

---

## Docker Compose Services

| Service | Image / Build | Ports | Purpose |
|---------|---------------|-------|---------|
| `mongodb` | `mongo:7` | 27017 | Database |
| `redis` | `redis:7-alpine` | 6379 | Celery broker |
| `backend` | `./backend` | 8000 | FastAPI app + bot webhook handlers |
| `worker` | `./backend` (Celery) | — | Background tasks (Phases 2-4) |
| `beat` | `./backend` (Celery Beat) | — | Periodic scheduler |
| `dashboard` | `./dashboard` | 3000 | Next.js system dashboard (admin/judges) |

---

## Verification & Testing

- **Unit tests:** TDEE calculation, GenBank FASTA parsing, phytochemical JSON loading, fallback mock switching — all in `backend/tests/`.
- **Integration test:** Full pipeline with a mock location triggering the H5N1 path → verify the adapted meal plan contains Quercetin-rich foods.
- **Bot demo flow:** Onboard a user via Telegram/Discord chat → set location to an active outbreak zone → user receives a proactive bot alert with adapted meal plan within ~30 seconds (or instantly via fallbacks). Judges observe the pipeline on the system dashboard simultaneously.
- **Fallback test:** Disconnect Amina CLI credentials → verify system gracefully serves pre-computed `.pdb` and docking results without breaking the demo.

---

## Key Decisions

| Decision | Chosen | Over | Reason |
|----------|--------|------|--------|
| User Interface | Telegram + Discord bots | Web forms | Conversational UX, meets users where they are, faster onboarding |
| System Dashboard | Next.js (React) | None | Visualization layer for admins/judges to see full pipeline |
| Language | Python (FastAPI) | Node.js | Best bioinformatics ecosystem (Biopython), async (httpx), Celery |
| Database | MongoDB | SQLite / PostgreSQL | Flexible schema for heterogeneous data |
| Phytochemical DB | Static curated JSON (~20-30 entries) | Full FooDB download | Feasible within hackathon timeframe |
| MVP strategy | All 5 phases live + fallback mocks | Partial live | Maximum impact while guaranteeing demo reliability |
| Bot MVP scope | Telegram + Discord | All 3 (+ WhatsApp) | WhatsApp requires Meta Business API approval — stretch goal |
| FLock Alliance | Simulated for demo | Full production FL | Stretch goal; simulation shows the concept |