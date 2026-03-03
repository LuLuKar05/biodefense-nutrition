

## Overview
A privacy-first platform combining personalized nutrition, bioinformatics, and decentralized AI that dynamically adjusts dietary recommendations based on local health threats (like viral outbreaks). The system detects threats, simulates viral structures, screens natural food compounds for neutralization, and pushes pathogen-resistant meal plans to users — **without ever storing user health data on a central server**.

**All user interaction happens through OpenClaw** — a self-hosted AI assistant gateway that runs **locally on the user's device** and natively connects to Telegram, Discord, WhatsApp, and 20+ other chat platforms. A separate Next.js web dashboard serves as the **system visualization layer** for admins and hackathon judges to observe the public threat intelligence pipeline.

**Privacy Model: Local-First, Zero-Knowledge Backend**
- **User health data (name, age, weight, allergies, diet, goals) NEVER leaves the user's machine.** It lives in OpenClaw's local session memory on their device.
- **TDEE/macro calculations run locally** on the user's machine via a bundled Python script called by the OpenClaw agent.
- **Meal plans are generated locally** by the LLM agent (using the user's own API key).
- **The backend is a zero-knowledge threat intelligence service** — it only serves public data (threat alerts, docking results by zone). It never receives or stores user PII.
- **The only data that leaves the user's machine:**
  - City-level zone queries to the threat API (e.g., `GET /threats?zone=NYC`) — no user identity attached
  - FLock model weights (NOT raw data) — federated learning by design
  - LLM API calls — user's own key, user's choice of provider (or local model via Ollama)

**Tech Stack:**
- **Chat Gateway:** [OpenClaw](https://github.com/openclaw/openclaw) (Node.js, runs locally) — handles all channel connections and runs AI agent
- **Custom Skill:** `biodefense-nutrition` SKILL.md — teaches the OpenClaw agent our domain logic
- **Local Scripts:** `scripts/` — Python scripts for TDEE/macro calculation, meal plan templates, FLock local training (run on user's machine via OpenClaw's bash tool)
- **Threat Intelligence API:** Python (FastAPI) — public, anonymous threat data only (no user PII)
- **System Dashboard:** Next.js (React) — admin/judge visualization of the threat pipeline
- **Threat Database:** MongoDB — stores only public threat data, docking results, zone info (ZERO user data)
- **Background Workers:** Celery + Redis — threat scanning, protein folding, molecular docking
- **Compute:** Amina CLI (cloud cluster) for ESMFold & DiffDock
- **Federated Learning:** FLock Alliance — local training, weight-only sharing
- **Containerization:** Docker Compose (for the threat intelligence backend)

**How OpenClaw Fits:**
OpenClaw runs locally on the user's device. It connects to messaging platforms as "channels" (bot tokens in a config file), runs a built-in Pi AI agent for conversation/session management, and supports custom "Skills" that teach the agent domain-specific behavior. The agent stores the user's profile in its **local session memory** — this data stays on the user's machine and is never sent to our backend.

- **Channels:** Telegram + Discord + WhatsApp configured via `openclaw.json`
- **Skill:** The `biodefense-nutrition` skill teaches the agent to collect health data (stored locally), calculate macros (locally), and query the public threat API (anonymously)
- **Local scripts:** Python scripts bundled in `scripts/` for TDEE calculation, meal template matching, and FLock local model training
- **Webhooks:** The threat API can push anonymous zone-level alerts to the user's OpenClaw instance

**MVP Strategy:** All 5 phases live with graceful fallback to pre-computed mocks at Phases 3-4. This ensures the demo always works while showing the real pipeline when it succeeds.

**Channel Support:** OpenClaw natively supports **22+ messaging platforms** from a single gateway. Active channels configured:
- **Primary:** Telegram, Discord, WhatsApp, Slack (tokens in `.env`)
- **Built-in:** WebChat (served from gateway at `:18789` — great for judge demos)
- **Optional:** Signal, Microsoft Teams, Google Chat, Matrix, LINE, Mattermost, IRC, iMessage, Twitch, and more
- **All channels run simultaneously** — users can interact from any platform and get proactive threat alerts on all of them.

See `SETUP_CHANNELS.md` for per-platform setup instructions.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   USER'S MACHINE (LOCAL)                        │
│                   All user data stays here                      │
│                                                                 │
│   User (Telegram / Discord / WhatsApp / Slack / WebChat / +17 more)     │
│                  │                                              │
│                  ▼                                              │
│   ┌──────────────────────────────────┐                          │
│   │       OpenClaw Gateway           │  ← Runs locally          │
│   │       (port 18789)               │                          │
│   │                                  │                          │
│   │  ┌────────────────────────────┐  │                          │
│   │  │ Biodefense Nutrition Skill │  │                          │
│   │  │ + Local Python scripts     │  │                          │
│   │  └────────────────────────────┘  │                          │
│   │                                  │                          │
│   │  Session Memory (LOCAL ONLY):    │                          │
│   │  • name, age, weight, height     │                          │
│   │  • allergies, diet type, goals   │                          │
│   │  • meal plans, meal logs         │                          │
│   └──────────────┬───────────────────┘                          │
│                  │                                              │
│   ┌──────────────┴───────────────────┐                          │
│   │  FLock Local Model               │  ← Trains on local data  │
│   │  (exports WEIGHTS only, not PII) │                          │
│   └──────────────┬───────────────────┘                          │
│                  │                                              │
└──────────────────┼──────────────────────────────────────────────┘
                   │
    ╔══════════════╧════════════════════════════════════╗
    ║  OUTBOUND (anonymous, no PII)                     ║
    ║                                                   ║
    ║  • GET /threats?zone=NYC     → Threat Intel API   ║
    ║  • FLock Alliance            → model weights only ║
    ║  • LLM API (user's own key) → meal plan gen       ║
    ╚══════════════╤════════════════════════════════════╝
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│          THREAT INTELLIGENCE BACKEND (public)         │
│          Zero-knowledge — no user data stored         │
│                                                       │
│   ┌──────────────────────────────────┐                │
│   │       FastAPI (port 8000)        │                │
│   │  Public threat data only         │                │
│   └──────────────┬───────────────────┘                │
│                  │                                    │
│          ┌───────┴───────┐                            │
│          ▼               ▼                            │
│   ┌─────────────┐  ┌──────────────┐                   │
│   │  MongoDB    │  │ Celery       │                   │
│   │  (threats   │  │ Workers      │                   │
│   │   only)     │  │ (Redis)      │                   │
│   └─────────────┘  └──────┬───────┘                   │
│                           │                           │
│           ┌───────────────┼───────────────┐           │
│           ▼               ▼               ▼           │
│     ┌──────────┐   ┌──────────┐   ┌──────────────┐   │
│     │ AQI API  │   │  NCBI    │   │  Amina CLI   │   │
│     │ ProMED   │   │ GenBank  │   │ (ESMFold /   │   │
│     └──────────┘   └──────────┘   │  DiffDock)   │   │
│                                   └──────────────┘   │
│                                                       │
│   ┌─────────────────┐                                 │
│   │  Next.js        │  ◄── System Dashboard           │
│   │  Dashboard      │      (judges see threat         │
│   └─────────────────┘       pipeline only)            │
│                                                       │
└──────────────────────────────────────────────────────┘
```

---

## Project Structure

```
OpenClawHack/
├── _specs/
│   └── overallSpec.md              # This file
├── openclaw/                       # OpenClaw Gateway configuration (runs on user's machine)
│   ├── openclaw.json               # Gateway config (channels, hooks, agent, skills)
│   └── workspace/                  # OpenClaw agent workspace
│       ├── AGENTS.md               # System prompt: "You are a Biodefense Nutritionist..."
│       ├── SOUL.md                 # Persona, tone, boundaries
│       ├── IDENTITY.md             # Agent name & emoji
│       ├── USER.md                 # Default user context
│       └── skills/
│           └── biodefense-nutrition/
│               └── SKILL.md        # Teaches agent: onboarding, local calc, threat queries
├── scripts/                        # Local Python scripts (run on user's machine via OpenClaw bash tool)
│   ├── calculate_macros.py         # TDEE + macro calculation (Mifflin-St Jeor)
│   ├── generate_meal_plan.py       # Template-based meal plan generator
│   ├── adapt_meal_plan.py          # Rewrite meal plan with biodefense foods
│   └── flock_local_train.py        # FLock local model training (exports weights only)
├── backend/                        # Threat Intelligence API (public, zero-knowledge)
│   ├── app/
│   │   ├── main.py                 # FastAPI entry point
│   │   ├── routers/
│   │   │   ├── threats.py          # Threat detection & docking result endpoints (public data)
│   │   │   └── dashboard.py        # Dashboard endpoints for judge visualization
│   │   └── services/
│   │       ├── threat_detection.py # AQI + public health querying
│   │       ├── genbank.py          # NCBI GenBank sequence fetching
│   │       └── flock_federated.py  # FLock Alliance weight aggregation (receives weights, NOT data)
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
│   │       ├── page.tsx            # Overview: active zones, pipeline status
│   │       ├── pipeline/           # Real-time pipeline visualization
│   │       └── threats/            # Threat map & docking results viewer
│   └── Dockerfile
├── data/
│   ├── phytochemicals.json         # Curated 20-30 phytochemical-to-food mappings
│   ├── meal_templates.json         # Fallback meal plan templates
│   ├── mock_sequences/             # Pre-downloaded FASTA files
│   ├── mock_pdb/                   # Pre-computed .pdb structures
│   └── mock_docking/               # Pre-computed docking result JSONs
├── docker-compose.yml              # MongoDB + Redis + backend + worker + dashboard (threat service only)
├── .env.example
├── .gitignore
└── instruction.md
```

---

## Phase 1: Get User Data & Target Body Goal (The Nutritionist Agent)

### Concept
Collects the user's health condition, preferred food types, target body goals, daily calorie burn, and previous nutritional intake. Calculates exact macronutrient needs and generates a highly personalized daily meal plan. **All user data stays LOCAL on the user's machine — nothing is stored on our servers.**

### Inputs
- Allergies
- Preferred diet type (vegan, keto, Mediterranean, etc.)
- Current weight (kg)
- Height (cm)
- Daily calorie burn
- Location (city-level only — used for zone-level threat lookups)
- Body goal (cut / bulk / maintain)

### Privacy Model
- User data is collected through conversation and stored in **OpenClaw's local session memory** (`~/.openclaw/agents/<agentId>/sessions/`).
- TDEE and macro calculations run **locally** via `scripts/calculate_macros.py`.
- Meal plans are generated **locally** by the LLM agent using the user's own API key.
- **ZERO user PII is sent to our backend.** The only outbound call is `GET /threats?zone=<city>` — an anonymous, city-level query.

### Implementation

**Local Script — `scripts/calculate_macros.py`**
- Calculate TDEE using Mifflin-St Jeor equation:
  - Male: `10 × weight(kg) + 6.25 × height(cm) − 5 × age − 161`
  - Female: `10 × weight(kg) + 6.25 × height(cm) − 5 × age + 5`
- Compute macro split (protein/carbs/fat) based on body goal:
  - **Cut:** 40% protein / 30% carbs / 30% fat
  - **Bulk:** 30% protein / 45% carbs / 25% fat
  - **Maintain:** 30% protein / 40% carbs / 30% fat
- Called by the OpenClaw agent via bash tool: `python scripts/calculate_macros.py --weight 75 --height 178 --age 28 --gender male --activity moderate --goal cut`
- Returns JSON to stdout: `{ "tdee": 2200, "protein_g": 220, "carbs_g": 165, "fat_g": 73 }`

**Local Script — `scripts/generate_meal_plan.py`**
- Template-based meal plan generator using `data/meal_templates.json`.
- Called by the agent: `python scripts/generate_meal_plan.py --diet vegan --calories 2200 --protein 220 --carbs 165 --fat 73 --allergies "nuts,dairy"`
- Returns a structured meal plan JSON to stdout.
- **Fallback:** The LLM agent can also compose a meal plan directly using its knowledge if the script is unavailable.

**OpenClaw Skill — `openclaw/workspace/skills/biodefense-nutrition/SKILL.md`**
The `biodefense-nutrition` skill teaches the agent the conversational onboarding flow:
1. Agent greets user → asks for **name, age, gender**.
2. Agent asks for **allergies** (free text or common list).
3. Agent asks for **diet type** (vegan, keto, Mediterranean, etc.).
4. Agent asks for **current weight (kg)** and **height (cm)**.
5. Agent asks for **daily activity level** (sedentary, light, moderate, active).
6. Agent asks for **body goal** (cut / bulk / maintain).
7. Agent asks for **location** (city name — for threat monitoring zone, NOT stored on server).
8. Agent confirms the profile summary → user confirms → **data stored in local session memory only**.
9. Agent runs `scripts/calculate_macros.py` locally → gets macros.
10. Agent runs `scripts/generate_meal_plan.py` locally (or composes via LLM) → sends meal plan to user.

All data stays in OpenClaw's local session. The agent remembers the user's profile across conversations because OpenClaw persists session transcripts locally.

**Daily Interaction (Post-Onboarding):**
- User can type what they ate → agent notes it in session memory (local only)
- User can ask for today's meal plan → agent generates locally
- User can update goals or weight → agent recalculates locally
- User can ask "any threats in my area?" → agent calls `GET /threats?zone=<city>` (anonymous query, no user identity)

**System Dashboard — `dashboard/` (Next.js)**
- NOT user-facing. For admins and hackathon judges.
- Shows the threat intelligence pipeline only: active zones, threat scans, protein folding status, docking results.
- **Does NOT show any user data** — because the backend doesn't have any.

### MVP Scope
- **Telegram + Discord** channels configured in OpenClaw for MVP. WhatsApp works out of the box with OpenClaw (Baileys) — stretch goal.
- User input via chat only (no wearable APIs or food image recognition).
- *Stretch:* Integrate Nutritionix API locally for natural language meal parsing.

---

## Phase 2: Threat Detection & Target Acquisition (The Biodefence Radar)

### Concept
Actively monitors the user's location against environmental APIs (AQI) and public health databases. If it detects a localized outbreak (e.g., H5N1), it pulls the genetic blueprint of the threat from NCBI GenBank.

### Inputs
- User location (city-level, from local session — the agent sends only the zone name to the API, no user identity)

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
- OpenClaw agent (via skill) + NCBI GenBank API + Public Health/AQI APIs.

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
The Nutritionist Agent receives molecular docking results from the public threat API, cross-references the winning ligand with everyday foods, and dynamically rewrites the user's meal plan to heavily feature these foods — **all locally on the user's machine** — while still aligning with the user's original Phase 1 weight/fitness goals.

### Inputs
- JSON output from Phase 4 (top ligands + food sources) — fetched from public threat API
- Phase 1 user baseline data (macros, allergies, diet type) — from local session memory

### Outputs
- Locally generated adapted meal plan featuring biodefense foods
- Alert message in chat explaining the threat and dietary changes

### Privacy: No User Data Leaves the Machine
The adaptation happens entirely locally:
1. Agent queries `GET /threats?zone=<city>` (anonymous — no user identity)
2. Agent queries `GET /threats/{id}/docking-results` (public data)
3. Agent runs `scripts/adapt_meal_plan.py` locally with user's macros (from session) + docking results
4. Agent presents the adapted plan to the user in chat

### Implementation

**Local Script — `scripts/adapt_meal_plan.py`**
- Takes the top docking results (e.g., Quercetin with 92% confidence).
- Looks up their `food_sources` from `data/phytochemicals.json` (bundled locally).
- Generates an adapted meal plan featuring biodefense foods while still meeting macro targets.
- Called by agent: `python scripts/adapt_meal_plan.py --calories 2200 --protein 220 --carbs 165 --fat 73 --diet vegan --allergies "nuts" --boost "quercetin,egcg,curcumin"`
- Returns an adapted meal plan JSON to stdout.
- **Alternative:** The LLM agent can compose the adapted plan directly using its knowledge of the docking results and user's macros from session memory.

**Threat Alert Flow (User-Initiated Polling)**
Since user data is local, the agent periodically checks for threats when the user interacts:
- On any conversation, the agent checks `GET /threats?zone=<user's city>` for new alerts.
- If a new threat is found since last check, the agent proactively informs the user:
  1. ⚠️ A clear but calm alert about the detected threat
  2. 🧬 A brief explanation of which natural compound was found effective
  3. 🍽️ The adapted meal plan featuring biodefense foods
  4. 💡 Why specific foods were added (e.g., "Red onions are rich in Quercetin, which showed strong binding to the viral protein")

**OpenClaw Webhook Alert (Optional — for demo)**
For the hackathon demo, the threat API can also push alerts to OpenClaw:
```
POST http://localhost:18789/hooks/agent
Authorization: Bearer <OPENCLAW_HOOKS_TOKEN>
{
  "message": "New threat detected: H5N1 in zone NYC. Top compound: Quercetin (92% confidence). Foods: Red Onions, Apples, Berries, Green Tea. Inform the user about this threat and help them adapt their meal plan using their local profile data.",
  "name": "ThreatAlert",
  "deliver": true,
  "channel": "last"
}
```
Note: This webhook message contains ONLY public threat data (zone + compounds). The agent then uses the user's LOCAL session data to compose a personalized response.

**System Dashboard — `/dashboard` (Judges View)**
- Show a **threat alert banner** on the admin dashboard with full pipeline details.
- Display docking results with highlighted "biodefense foods" and their associated phytochemical + confidence score.
- Real-time map showing which zones have active threats.
- **No user data visible** — only public threat intelligence data.

**FLock Alliance — Federated Learning (Privacy-Preserving)**
`scripts/flock_local_train.py` (runs locally on user's machine):
- Collects anonymized efficacy signals from local session: symptom frequency changes, adherence to adapted meals.
- Trains a lightweight local model (logistic regression or small NN) mapping `{ phytochemical_consumed, zone, outcome }`.
- **Exports model WEIGHTS only** — never raw data, never user PII.
- Pushes weights to FLock Alliance for federated aggregation.
- Pulls aggregated weights back to refine confidence score adjustments locally.

`backend/app/services/flock_federated.py` (on the threat API server):
- Receives model weights from participating users (NOT raw data).
- Aggregates weights via federated averaging (FedAvg).
- Serves aggregated model weights back to users.
- **The server never sees individual user data — only mathematical weight vectors.**

This is the **ideal use case for federated learning** — users keep their health data private while still contributing to a shared intelligence model that improves compound-to-food confidence rankings for everyone.

**For hackathon demo:** Simulate with a before/after visualization showing how federated weight updates shift the confidence rankings without revealing individual data.

---

## API Endpoints Summary (Threat Intelligence API — Zero-Knowledge)

All endpoints serve **public threat data only**. No user PII is received or stored.

| Method | Endpoint | Phase | Description |
|--------|----------|-------|-------------|
| `GET` | `/api/threats?zone={zone}` | 2 | List active threats for a geographic zone (anonymous) |
| `GET` | `/api/threats/{id}/structure` | 3 | Get PDB structure file or status |
| `GET` | `/api/threats/{id}/docking-results` | 4 | Get top-N ligand results with food sources |
| `POST` | `/api/flock/weights` | 5 | Submit local model weights (NOT user data) for federated aggregation |
| `GET` | `/api/flock/aggregated-weights` | 5 | Pull aggregated model weights |
| `GET` | `/api/dashboard/pipeline-status` | Dashboard | Real-time pipeline status for judge view |
| `GET` | `/api/dashboard/zone-summary` | Dashboard | Active threats per zone |

**Removed endpoints (privacy):** No `/api/users/*` endpoints. User data never reaches the server. Onboarding, meal plans, meal logging, and symptom reporting all happen locally on the user's machine via OpenClaw session memory and local scripts.

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
# ── Threat Intelligence Backend ──
MONGODB_URI=mongodb://localhost:27017/biodefense
REDIS_URL=redis://localhost:6379/0

# ── External APIs (backend only) ──
NCBI_API_KEY=
AQI_API_KEY=
AMINA_API_KEY=
FLOCK_ALLIANCE_KEY=

# ── OpenClaw Gateway (user's local machine) ──
TELEGRAM_BOT_TOKEN=                # OpenClaw reads this for channels.telegram
DISCORD_BOT_TOKEN=                 # OpenClaw reads this for channels.discord
OPENCLAW_HOOKS_TOKEN=              # For threat alert webhooks (optional)
THREAT_API_URL=https://your-threat-api.com  # Public threat intelligence API URL

# ── LLM Provider (user's own key — runs locally) ──
OPENAI_API_KEY=                    # or ANTHROPIC_API_KEY — user's choice
```

---

## Docker Compose Services (Threat Intelligence Backend Only)

These services run the **public threat intelligence pipeline**. They contain ZERO user data.

| Service | Image / Build | Ports | Purpose |
|---------|---------------|-------|---------|
| `mongodb` | `mongo:7` | 27017 | Threat data only (zones, sequences, docking results) |
| `redis` | `redis:7-alpine` | 6379 | Celery broker |
| `backend` | `./backend` | 8000 | FastAPI threat intelligence API |
| `worker` | `./backend` (Celery) | — | Background tasks (Phases 2-4) |
| `beat` | `./backend` (Celery Beat) | — | Periodic threat scanner |
| `dashboard` | `./dashboard` | 3000 | Next.js dashboard (threat pipeline view for judges) |

**Note:** OpenClaw is NOT in Docker Compose — it runs **locally on the user's device** (installed via `npm install -g openclaw@latest`). This ensures all user data stays on their machine.

---

## Verification & Testing

- **Unit tests:** TDEE calculation (local script), GenBank FASTA parsing, phytochemical JSON loading, fallback mock switching — in `backend/tests/` and `scripts/` tests.
- **Privacy test:** Inspect all outbound HTTP calls — verify ZERO user PII leaves the local machine. Only zone-name queries and model weights are sent.
- **Integration test:** Full pipeline with a mock zone triggering the H5N1 path → verify docking results reach the local agent → adapted meal plan contains Quercetin-rich foods.
- **Chat demo flow:** Onboard a user via Telegram/Discord (OpenClaw local) → set location to an active outbreak zone → agent checks `GET /threats?zone=<city>` → detects threat → adapts meal plan locally → presents it in chat. Judges observe the threat pipeline on the dashboard simultaneously.
- **Fallback test:** Disconnect Amina CLI credentials → verify system gracefully serves pre-computed `.pdb` and docking results without breaking the demo.
- **FLock test:** Run `scripts/flock_local_train.py` → verify it exports only weight vectors → submit to `POST /api/flock/weights` → verify NO user data in payload.

---

## Key Decisions

| Decision | Chosen | Over | Reason |
|----------|--------|------|--------|
| **Privacy model** | **Local-first, zero-knowledge backend** | Centralized user storage | User health data is confidential — never leaves the user's machine |
| Chat Gateway | OpenClaw (runs locally on user's device) | Custom bot code | Native multi-channel support, built-in AI agent, local session storage — all data stays on device |
| User data storage | OpenClaw local session memory | MongoDB on server | Privacy: no PII on any server, ever |
| Computation | Local scripts + LLM (user's key) | Server-side compute | TDEE, macros, meal plans all run on user's machine |
| Backend role | Public threat intelligence API only | Full-stack user+threat API | Zero-knowledge: backend serves anonymous public data, never sees user PII |
| AI Agent | OpenClaw's Pi agent + custom Skill | Z.ai standalone | OpenClaw provides local session persistence + multi-channel routing |
| FLock Alliance | **Local training, weight-only sharing** | Centralized data collection | Perfect federated learning use case: improve shared model without sharing private data |
| System Dashboard | Next.js (React) — threat pipeline only | Full user dashboard | Dashboard shows only public threat data; no user data to display |
| Language | Python (FastAPI + local scripts) | Node.js | Best bioinformatics ecosystem (Biopython), local scripts for TDEE/macros |
| Threat Database | MongoDB (threats + docking only) | User data + threats | DB stores only public scientific data — zones, sequences, docking scores |
| Phytochemical DB | Static curated JSON (~20-30 entries) | Full FooDB download | Bundled locally with OpenClaw, no network needed |
| MVP strategy | All 5 phases live + fallback mocks | Partial live | Maximum impact while guaranteeing demo reliability |
| Channel MVP scope | Telegram + Discord | All 3 (+ WhatsApp) | WhatsApp works with OpenClaw (Baileys) but is a stretch goal |