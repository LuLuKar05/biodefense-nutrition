

## Overview

> **Last Updated**: March 2026 — v2.0 (Webhook Architecture + Amina AI + WHO DON)

A privacy-first platform combining personalized nutrition, bioinformatics, and decentralized AI that dynamically adjusts dietary recommendations based on local health threats (like viral outbreaks). The system detects threats, analyses pathogen proteins, screens natural food compounds for antagonism, and **proactively pushes** pathogen-resistant meal plans to users — **without ever storing user health data on a central server**.

**Three-layer architecture:**
1. **OpenClaw Gateway** — multi-channel chat gateway (Telegram, Discord, WhatsApp, Slack, WebChat, 20+ more). Runs locally on user's device. Receives messages from all channels and delivers replies.
2. **Agent Orchestrator** — Python-based personal agent. Routes user intent to specialized handlers (Onboarding, Nutrition, Threat). Each handler uses **FLock API** as its LLM brain. Includes a **webhook receiver** (port 8200) to receive proactive threat alerts from Layer 3.
3. **Threat Intelligence Backend** — zero-knowledge FastAPI service (port 8100). Fetches real WHO outbreak data, NCBI protein sequences, runs Amina AI protein analysis, maps threats to nutrition. **Fires webhooks** to subscribers when threats change. Never receives user PII.

**Privacy Model: Local-First, Zero-Knowledge Backend**
- **User health data (name, age, weight, allergies, diet, goals) NEVER leaves the user's machine.** It lives in local JSON files (`data/profiles/<chat_id>.json`) on their device.
- **TDEE/macro calculations run locally** via Python tools in `agents/tools/`.
- **Meal plans are generated locally** by the Nutrition Agent (using FLock API for LLM reasoning).
- **The backend is a zero-knowledge threat intelligence service** — it only serves public data (threat alerts, protein analyses, nutrient recommendations). It never receives or stores user PII.
- **The only data that leaves the user's machine:**
  - User messages sent to FLock API for LLM inference (same as any chatbot using an LLM)
  - City name + anonymous callback URL to the threat API — no user identity attached
  - FLock model weights (NOT raw data) — federated learning by design

**Tech Stack:**
- **Chat Gateway:** [OpenClaw](https://github.com/openclaw/openclaw) (Node.js, runs locally) — handles all channel connections, forwards messages to agent orchestrator via hooks
- **Agent Orchestrator:** Python — per-user personal agent, routes intent to handlers, receives proactive alerts (`agents/orchestrator.py`)
- **Webhook Receiver:** FastAPI on port 8200 — receives proactive threat alerts from Layer 3
- **LLM Brain:** [FLock API](https://platform.flock.io) (`api.flock.io/v1`) — OpenAI-compatible inference, powers all agent natural language understanding and generation
- **Agents:** Python modules in `agents/` — Onboarding Agent, Nutrition Agent, Threat Handler (in orchestrator)
- **Agent Tools:** Pure Python functions in `agents/tools/` — macro calculation, meal planning, meal management, profile management, validators, circuit breaker
- **Threat Intelligence Backend:** Python (FastAPI, port 8100) — autonomous threat pipeline with subscriber registry and webhook fire
- **WHO DON API:** `who.int/api/news/diseaseoutbreaknews` — real outbreak data via OData (public, no auth)
- **NCBI Entrez API:** Real protein sequence fetching for detected pathogens
- **Amina AI:** Local amino acid analysis engine — 8-factor compound scoring against pathogen proteins
- **Research Agent:** FLock LLM-powered research for unknown diseases not in the nutrition DB
- **Federated Learning:** FLock Alliance — local training, weight-only sharing (planned)

**How the Three Layers Connect:**

```
User (any channel)
    │
    ▼
OpenClaw Gateway (port 18789, runs locally)
    │  receives message from Telegram/Discord/WhatsApp/etc.
    │  forwards to hooks endpoint (or Telegram polling in MVP)
    ▼
Agent Orchestrator (Python, runs locally)
    │  Per-user personal agent
    │  checks user state → routes to correct handler
    │
    ├──► Onboarding Agent ──► FLock API (extracts profile fields naturally)
    │         │                    │
    │         │                    ▼
    │         │                Validators (validate_name, validate_age, etc.)
    │         │                    │
    │         │                    ▼
    │         │                data/profiles/<chat_id>.json (LOCAL ONLY)
    │         │
    │         └──► On profile complete → auto-subscribe to Layer 3 for user's city
    │
    ├──► Nutrition Agent ───► FLock API + agents/tools/macro_calculator.py
    │                         + agents/tools/meal_planner.py
    │                         + agents/tools/meal_manager.py
    │
    ├──► Threat Handler ────► GET /threats/{city}/report (Layer 3 pre-formatted)
    │         │                  │
    │         │                  ├─► _ensure_subscribed() → POST /subscribe to Layer 3
    │         │                  └─► Forward report to user + chain to nutrition
    │         │
    │         └──► Proactive path (webhook from Layer 3):
    │              POST /threat-alert on :8200
    │              → Push alert to user's Telegram
    │              → Auto-chain to nutrition agent → adapted meal plan
    │
    ▼
Agent returns reply text
    │
    ▼
OpenClaw Gateway → sends back to SAME channel (or Telegram direct in MVP)
    │
    ▼
User sees reply

    ═══════════════════════════════════════════════

    Layer 3: Threat Intelligence Backend (port 8100)
    ├── Runs autonomously (hourly refresh, no user triggers needed)
    ├── WHO DON API → real outbreak data (3-tier geographic filtering)
    ├── NCBI Entrez → pathogen protein sequences
    ├── Amina AI → amino acid analysis + compound scoring
    ├── Research Agent → FLock LLM for unknown diseases
    ├── Nutrient Mapper → 3-tier hybrid: disease_db → research → category
    ├── Change Detection → SHA-256 fingerprint diffing
    └── Webhook Fire → POST to subscriber callbacks on threat change
```

**Why FLock API (not OpenAI/Ollama):**
- FLock is an **OpenAI-compatible LLM inference platform** (`api.flock.io/v1`) — uses the same chat/completions format
- Lower cost than mainstream inference APIs
- Decentralized AI aligns with hackathon theme
- All agents share the same FLock API key — single integration point
- Model: `qwen3-30b-a3b-instruct-2507` (or other models available on FLock platform)

**MVP Strategy:** Phases 1-2 are fully live with real data sources (WHO DON, NCBI, Amina AI). Phase 5 (proactive alerts + meal adaptation) is also fully live via the webhook architecture. Phases 3-4 (ESMFold 3D structure + DiffDock molecular docking) use the Amina AI local analysis as a functional equivalent, with full 3D pipeline planned as a future enhancement. Agents gracefully degrade to step-by-step mode if FLock API is unavailable.

**Channel Support:** OpenClaw natively supports **22+ messaging platforms** from a single gateway. Active channels configured:
- **Primary:** Telegram (active — bot `@ClawHackNutritionistBot`)
- **Built-in:** WebChat (served from gateway at `:18789` — great for judge demos)
- **Optional:** Discord, WhatsApp, Slack, Signal, Microsoft Teams, Google Chat, Matrix, LINE, Mattermost, IRC, iMessage, Twitch, and more
- **All channels run simultaneously** — users can interact from any platform and get proactive threat alerts on all of them.

See `SETUP_CHANNELS.md` for per-platform setup instructions.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   USER'S MACHINE (LOCAL)                        │
│                   All user data stays here                      │
│                                                                 │
│   User (Telegram / Discord / WhatsApp / Slack / WebChat / +17) │
│                  │                                              │
│                  ▼                                              │
│   ┌──────────────────────────────────────┐                      │
│   │       OpenClaw Gateway               │  ← Runs locally     │
│   │       (port 18789, Node.js)          │                      │
│   │                                      │                      │
│   │  Receives messages from ALL channels │                      │
│   │  Forwards to hooks endpoint          │                      │
│   │  Delivers replies back to user       │                      │
│   └──────────────┬───────────────────────┘                      │
│                  │ webhook POST (or Telegram polling in MVP)    │
│                  ▼                                              │
│   ┌──────────────────────────────────────┐                      │
│   │   Agent Orchestrator (Personal)      │  ← Python, local    │
│   │   (agents/orchestrator.py)           │                      │
│   │                                      │                      │
│   │   Per-user personal agent:           │                      │
│   │   Routes intent to handlers          │                      │
│   │                                      │                      │
│   │  ┌────────────────────────────────┐  │                      │
│   │  │ Onboarding Agent              │  │                      │
│   │  │ (natural profile collection)  │──┼──► FLock API        │
│   │  │ → auto-subscribe on complete  │  │   (api.flock.io/v1) │
│   │  └────────────────────────────────┘  │   Model: qwen3-30b  │
│   │  ┌────────────────────────────────┐  │                      │
│   │  │ Nutrition Agent               │  │                      │
│   │  │ (TDEE, macros, meal plans,    │──┼──► FLock API        │
│   │  │  meal logging, adaptation)    │  │                      │
│   │  └────────────────────────────────┘  │                      │
│   │  ┌────────────────────────────────┐  │                      │
│   │  │ Threat Handler                │  │                      │
│   │  │ (calls Layer 3, forwards      │──┼──► Layer 3 API      │
│   │  │  report, chains to nutrition) │  │   (GET /report)     │
│   │  └────────────────────────────────┘  │                      │
│   │  ┌────────────────────────────────┐  │                      │
│   │  │ Webhook Receiver (port 8200)  │  │                      │
│   │  │ Receives proactive alerts     │◄─┼──  Layer 3 webhook  │
│   │  │ → Push to Telegram            │  │   POST /threat-alert│
│   │  │ → Auto-chain meal adaptation  │  │                      │
│   │  └────────────────────────────────┘  │                      │
│   │                                      │                      │
│   │  Shared Tools:                       │                      │
│   │  agents/tools/macro_calculator.py    │                      │
│   │  agents/tools/meal_planner.py        │                      │
│   │  agents/tools/meal_manager.py        │                      │
│   │  agents/tools/profile_manager.py     │                      │
│   │  agents/tools/validators.py          │                      │
│   │  agents/tools/circuit_breaker.py     │                      │
│   └──────────────┬───────────────────────┘                      │
│                  │                                              │
│   Local Data (NEVER leaves machine):                            │
│   data/profiles/<chat_id>.json  — user health profiles          │
│   data/meals/<chat_id>.json     — meal logs                     │
│   data/meal_templates.json      — meal plan templates           │
│   data/phytochemicals.json      — 15 compounds + food sources   │
│   data/disease_nutrition_db.json— 11 diseases, evidence-based   │
│                                                                 │
└──────────────────┼──────────────────────────────────────────────┘
                   │
    ╔══════════════╧════════════════════════════════════╗
    ║  OUTBOUND (minimal, no PII)                       ║
    ║                                                   ║
    ║  • FLock API (api.flock.io/v1) → LLM inference   ║
    ║    (user messages for conversation — same as      ║
    ║     using any chatbot with an LLM)                ║
    ║  • City name + callback URL → Threat Intel API    ║
    ║    (anonymous — no user identity)                 ║
    ╚══════════════╤════════════════════════════════════╝
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│    THREAT INTELLIGENCE BACKEND (Layer 3, port 8100)   │
│    Zero-knowledge — processes only public data        │
│                                                       │
│   ┌──────────────────────────────────┐                │
│   │     FastAPI (port 8100)          │                │
│   │  • 9 API endpoints               │                │
│   │  • Subscriber registry            │                │
│   │  • Change detection + webhooks    │                │
│   │  • Report formatter               │                │
│   └──────────────┬───────────────────┘                │
│                  │                                    │
│   ┌──────────────┴───────────────────┐                │
│   │  Background Scheduler (hourly)   │                │
│   │                                  │                │
│   │  Step 1: WHO DON OData API       │                │
│   │  Step 2: NCBI Entrez proteins    │                │
│   │  Step 3: Amina AI + Research     │                │
│   │  Step 4: Refresh 25 cities       │                │
│   │  Step 5: Diff + fire webhooks    │                │
│   └──────────────┬───────────────────┘                │
│                  │                                    │
│          ┌───────┼──────────┐                         │
│          ▼       ▼          ▼                         │
│   ┌──────────┐ ┌──────────┐ ┌──────────────┐         │
│   │ WHO DON  │ │  NCBI    │ │OpenWeatherMap│         │
│   │ OData API│ │ Entrez   │ │ AQI API      │         │
│   │(outbreaks│ │(proteins)│ │(optional)    │         │
│   └──────────┘ └──────────┘ └──────────────┘         │
│                                                       │
│   Local data sources:                                 │
│   • disease_nutrition_db.json (11 diseases)           │
│   • phytochemicals.json (15 compounds + SMILES)       │
│                                                       │
└──────────────────────────────────────────────────────┘
```

---

## Project Structure

```
OpenClawHack/
├── _specs/
│   └── overallSpec.md              # This file — overall system spec
│
├── _design/
│   ├── threatDetector.md           # Threat detection system design
│   ├── onboardAgent.md             # Onboarding agent design
│   └── FlowChat.md                 # User flow diagrams
│
├── openclaw/                       # OpenClaw Gateway config (multi-channel, runs locally)
│   ├── openclaw.json               # Gateway config (channels, hooks)
│   └── workspace/                  # OpenClaw agent workspace
│       ├── AGENTS.md               # System prompt: "You are a Biodefense Nutritionist..."
│       ├── SOUL.md                 # Persona, tone, boundaries
│       ├── IDENTITY.md             # Agent name & emoji
│       ├── USER.md                 # Default user context
│       └── skills/
│           └── biodefense-nutrition/
│               └── SKILL.md        # Teaches agent: onboarding, local calc, threat queries
│
├── agents/                         # Agent Orchestrator + Sub-Agents
│   ├── __init__.py
│   ├── orchestrator.py             # Personal agent — central router + webhook receiver
│   │                               #   Per-user city registry, proactive alert handler,
│   │                               #   auto-subscribe, intent detection, chaining
│   ├── onboarding_agent.py         # Natural profile collection via FLock API
│   │                               #   Validates with existing validators
│   │                               #   Saves to data/profiles/<chat_id>.json
│   ├── nutrition_agent.py          # TDEE/macro calc, meal plan generation,
│   │                               #   meal logging, threat-adapted planning
│   └── tools/                      # Shared tools (pure functions, no LLM)
│       ├── __init__.py
│       ├── macro_calculator.py     # Mifflin-St Jeor TDEE + macro splits
│       ├── meal_planner.py         # Meal plan generation logic
│       ├── meal_manager.py         # Meal logging + tracking
│       ├── profile_manager.py      # Profile CRUD (data/profiles/)
│       ├── validators.py           # validate_name, validate_age, etc.
│       └── circuit_breaker.py      # Fault tolerance for external API calls
│
├── threat_backend/                 # Threat Intelligence Backend (Layer 3)
│   ├── __init__.py
│   ├── __main__.py                 # Entry: python -m threat_backend → uvicorn on :8100
│   ├── server.py                   # FastAPI app + scheduler + 9 endpoints
│   │                               #   + subscriber registry + change detection
│   │                               #   + webhook fire + report formatter
│   ├── cities.py                   # 25 UK cities with lat/lon
│   ├── aqi_fetcher.py              # AQI: real OpenWeatherMap API or mock fallback
│   ├── outbreak_fetcher.py         # WHO DON OData API + 3-tier geographic filtering
│   ├── outbreak_mock.py            # Legacy mock fallback (still available)
│   ├── sequence_fetcher.py         # NCBI Entrez protein sequence fetcher + cache
│   ├── amina_ai.py                 # Amino-acid Intelligence for Nutritional Antagonists
│   │                               #   Protein analysis + compound scoring + LLM enrichment
│   ├── research_agent.py           # FLock LLM research for unknown diseases
│   └── nutrient_mapper.py          # 3-tier hybrid: disease_db → research → category
│
├── data/
│   ├── disease_nutrition_db.json   # 11 diseases — evidence-based compound mappings
│   ├── phytochemicals.json         # 15 phytochemicals with SMILES + food sources
│   ├── meal_templates.json         # Fallback meal plan templates
│   ├── profiles/                   # Local-only user profiles (gitignored)
│   ├── meals/                      # Local-only meal logs (gitignored)
│   ├── mock_sequences/             # Pre-downloaded FASTA files (fallback)
│   └── mock_docking/               # Pre-computed docking JSONs (fallback)
│
├── .env                            # Environment variables (gitignored)
├── .gitignore
├── requirements.txt
└── instruction.md
```

---

## Phase 1: Get User Data & Target Body Goal (The Nutritionist Agent) — ✅ IMPLEMENTED

### Concept
Collects the user's health condition, preferred food types, target body goals, daily calorie burn, and previous nutritional intake. Calculates exact macronutrient needs and generates a highly personalized daily meal plan. **All user data stays LOCAL on the user's machine — nothing is stored on our servers.**

### Inputs
- Allergies
- Preferred diet type (vegan, keto, Mediterranean, etc.)
- Current weight (kg)
- Height (cm)
- Age
- Biological sex (male / female)
- Body goal (cut / bulk / maintain)
- Location (city-level only — used for zone-level threat lookups + proactive alert subscription)

### Privacy Model
- User data is collected through natural conversation and stored in **local JSON files** (`data/profiles/<chat_id>.json`).
- TDEE and macro calculations run **locally** via `agents/tools/macro_calculator.py`.
- Meal plans are generated **locally** by the Nutrition Agent using FLock API for LLM reasoning.
- **ZERO user PII is sent to our backend.** The only outbound calls from the threat path are `GET /threats/{city}/report` — anonymous, city-level queries and anonymous callback URL registration.

### Implementation

**Agent — `agents/onboarding_agent.py` (~660 lines)**
- Uses FLock API (`api.flock.io/v1`, model `qwen3-30b-a3b-instruct-2507`) for natural conversation
- System prompt instructs agent to extract profile fields from user messages as structured JSON
- User can give multiple fields in one message: "I'm Sarah, 28, trying to lose weight" → extracts name, age, goal
- All extracted fields pass through **validators** (`agents/tools/validators.py`):
  - `validate_name`: 2-50 chars, letters/spaces/hyphens only
  - `validate_age`: 13-120, strips "years old" etc.
  - `validate_sex`: maps m/male/man → "male", f/female/woman → "female"
  - `validate_weight`: 20-300 kg, strips unit suffixes
  - `validate_height`: 100-250 cm, strips unit suffixes
  - `validate_allergies`: comma-separated list or "none"
  - `validate_diet`: maps to mediterranean/keto/vegan/standard
  - `validate_goal`: maps to cut/bulk/maintain
  - `validate_city`: 2-100 chars
- If FLock API unavailable: graceful fallback to step-by-step mode (asks one field at a time)
- Profile saved as `data/profiles/<chat_id>.json` — NEVER sent to any server
- **On profile complete**: Orchestrator auto-subscribes user's city to Layer 3 for proactive threat alerts

**Agent — `agents/nutrition_agent.py` (~410 lines)**
- Called after onboarding complete, or when user asks about macros/meals
- Calls `agents/tools/macro_calculator.py` — pure Mifflin-St Jeor:
  - Male: `10 × weight(kg) + 6.25 × height(cm) − 5 × age + 5`
  - Female: `10 × weight(kg) + 6.25 × height(cm) − 5 × age − 161`
- Macro split by goal:
  - **Cut:** 40% protein / 30% carbs / 30% fat
  - **Bulk:** 30% protein / 45% carbs / 25% fat
  - **Maintain:** 30% protein / 40% carbs / 30% fat
- Uses FLock API to compose natural meal plans matching computed macros, user allergies, diet type
- Supports meal logging: user types what they ate → notes it, shows remaining macros
- **Threat-adapted meal planning**: When chained from threat handler, receives `CHAIN_CONTEXT` with boost nutrients and adapts meal plan to feature biodefense foods
- Falls back to `data/meal_templates.json` if FLock unavailable

**Orchestrator — `agents/orchestrator.py` (~600 lines)**
- Per-user personal agent — one logical instance per Telegram chat_id
- On any message: checks `data/profiles/<chat_id>.json`
- If profile incomplete → route to Onboarding Agent
- If profile complete → detect intent → route to Nutrition/Threat handler
- Intent detection: slash commands (`/threats`, `/meal`, `/profile`, `/help`) + keyword-based + LLM-assisted for ambiguous messages
- **Webhook receiver on port 8200**: Receives proactive alerts from Layer 3 and pushes to users
- **Auto-subscribe on startup**: Re-registers all existing user profiles to Layer 3

**OpenClaw Integration**
- OpenClaw Gateway receives messages from all channels (Telegram, Discord, WhatsApp, etc.)
- Forwards to Python agent orchestrator via hooks webhook
- Agent processes message, returns reply text
- OpenClaw delivers reply back to the same channel
- Channel-agnostic: agents never need to know which platform the user is on
- **MVP**: Direct Telegram polling (no OpenClaw gateway needed — orchestrator has built-in Telegram bot)

**Daily Interaction (Post-Onboarding):**
- User can type what they ate → Nutrition Agent notes it and shows remaining macros
- User can ask for today's meal plan → Nutrition Agent generates locally via FLock
- User can update goals or weight → Onboarding Agent updates profile locally
- User can ask "any threats in my area?" → Threat Handler calls Layer 3, returns pre-formatted report
- **Proactive alerts**: User automatically receives threat alerts when their city's threats change (no action needed)
- All conversation flows through FLock API for natural language — no robotic Q&A

### MVP Scope
- **Telegram** channel active via built-in polling. OpenClaw gateway available for multi-channel expansion.
- User input via chat only (no wearable APIs or food image recognition).
- *Stretch:* Integrate Nutritionix API locally for natural language meal parsing.

---

## Phase 2: Threat Detection & Target Acquisition (The Biodefence Radar) — ✅ IMPLEMENTED

### Concept
Autonomously monitors the user's location against environmental APIs (AQI) and **real WHO Disease Outbreak News** (OData API). Detects outbreaks with 3-tier geographic filtering (UK → EURO → Global), then pulls the genetic blueprint of the threat from **NCBI GenBank** via Entrez API.

### Inputs
- User location (city-level, from local profile — the agent sends only the city name to the API, no user identity)

### Outputs
- Real WHO outbreak data per city (with severity tiers)
- Raw Amino Acid Sequences (FASTA) of circulating pathogens' target proteins from NCBI
- AQI data (real via OpenWeatherMap or mock)

### Implementation

**Backend — `threat_backend/outbreak_fetcher.py`**
- Fetches real WHO Disease Outbreak News via OData API: `https://www.who.int/api/news/diseaseoutbreaknews`
- Public API, no auth required, cached for 6 hours
- **3-tier geographic filtering:**
  - Tier 1 (UK): Region = United Kingdom → severity "high"
  - Tier 2 (EURO): Region = European Region → severity "moderate"
  - Tier 3 (Global): All WHO DON items → severity "low"
- Strips HTML from WHO content fields (OverviewSection, Advice, Assessment, Epidemiology)
- Extracts disease key via `extract_disease_key()` for nutrition DB lookup
- Returns enriched outbreak dicts with WHO context fields

**Backend — `threat_backend/sequence_fetcher.py`**
- When a disease is detected that's in `disease_nutrition_db.json`, fetches the latest amino acid sequence from NCBI via Biopython's `Entrez.esearch` + `Entrez.efetch`
- Parses the FASTA response and extracts the amino acid sequence string
- In-memory cache to avoid redundant fetches across refresh cycles
- Returns: `{protein_id, title, organism, length, sequence}`

**Backend — `threat_backend/aqi_fetcher.py`**
- Real mode: OpenWeatherMap Air Pollution API (set `OWM_API_KEY`)
- Mock mode: Deterministic city-aware simulation (no API key needed)
- AQI index 1-5, threats at index ≥ 3

**Backend — `threat_backend/server.py`**
- Background scheduler runs every 3600 seconds (1 hour)
- 5-step pipeline: WHO outbreaks → NCBI sequences → Amina AI/Research → city refresh → change detection + webhooks
- Caches all data in-memory per city
- First refresh is "initial load" — subsequent changes fire webhooks to subscribers

**Fallback Mock**
- `threat_backend/outbreak_mock.py` — legacy seasonal mock data, still available as fallback
- Pre-downloaded sequences stored in `data/mock_sequences/` (e.g., `h5n1_hemagglutinin.fasta`)

### Usage
- **Autonomous**: Runs hourly with no user interaction needed
- **On-demand**: User sends `/threats` → Orchestrator calls `GET /threats/{city}/report`
- **Proactive**: Layer 3 fires webhook to Layer 2 when threats change → pushed to Telegram

---

## Phase 3: Protein Analysis (Understanding the Threat) — ✅ IMPLEMENTED (Amina AI)

### Concept
Analyses the amino acid sequence to understand the pathogen protein's properties — composition, structural hints, binding opportunities, and pathogen-associated motifs. This drives the compound scoring in Phase 4.

**Current Implementation**: Amina AI (`threat_backend/amina_ai.py`) — a local amino acid intelligence engine that performs protein analysis without requiring cloud compute or 3D structure prediction.

**Future Enhancement**: ESMFold via Amina CLI for full 3D structure prediction.

### Inputs
- Amino Acid Sequence from Phase 2 (NCBI protein sequence string)

### Outputs
- Protein composition analysis (amino acid percentages, hydrophobic content, charge balance)
- 15 pathogen-associated motif patterns detected
- Binding opportunity analysis (surface accessibility, pocket estimates)
- Structural hints (helix/sheet propensity, disorder tendency)

### Implementation

**Backend — `threat_backend/amina_ai.py` → `analyse_protein(sequence)`**
- Amino acid composition: calculates percentage of each amino acid
- Hydrophobic content: sum of A, V, I, L, M, F, W, P
- Charge analysis: positive (R, K, H) vs negative (D, E)
- **15 pathogen motif patterns**: Fusion peptide, glycosylation sites, protease cleavage, zinc fingers, RNA-binding, transmembrane helices, signal peptides, receptor binding, hemagglutinin, neuraminidase, integrase, reverse transcriptase, capsid assembly, endonuclease, polymerase
- Binding opportunity estimation based on hydrophobic regions and charge distribution
- Structural hints: helix propensity (A, E, L, M), sheet propensity (V, I, Y, F, W, T), disorder tendency (P, G, S, Q, N)

**Fallback**
- If protein sequence unavailable → skip analysis, fall through to research agent or category-based nutrient mapping

---

## Phase 4: Phytochemical Compound Scoring (Nutritional Antagonist Screening) — ✅ IMPLEMENTED (Amina AI)

### Concept
Scores food-derived phytochemical compounds against the analysed pathogen protein to identify which natural compounds are most likely to antagonise the threat. Uses an 8-factor scoring model rather than full molecular docking simulation.

**Current Implementation**: Amina AI `score_compounds_against_protein()` — 8-factor heuristic scoring of 15 phytochemicals.

**Future Enhancement**: DiffDock molecular docking for full physics-based binding simulation.

### Inputs
- Protein analysis from Phase 3 (Amina AI analyse_protein output)
- 15 phytochemical compounds from `data/phytochemicals.json` (with SMILES strings)

### Outputs
- Ranked list of compounds by antagonism score
- Per-compound breakdown of 8 scoring factors
- LLM-enriched nutrition strategy (via FLock API)

### Implementation

**Backend — `threat_backend/amina_ai.py` → `score_compounds_against_protein(analysis)`**

**8 Scoring Factors:**
| Factor | Description |
|--------|-------------|
| `charge_complementarity` | Electrostatic match between compound and protein surface |
| `hydrophobic_match` | Hydrophobic surface interaction potential |
| `size_fit` | Molecular weight vs estimated binding pocket size |
| `motif_relevance` | Compound targets detected pathogen motifs |
| `aromatic_stacking` | Pi-pi interaction potential |
| `hbond_potential` | Hydrogen bonding capacity |
| `flexibility_match` | Conformational adaptability |
| `literature_boost` | Known antiviral/antibacterial evidence from research |

**15 Compounds Scored:**

| Phytochemical | SMILES (in phytochemicals.json) | Food Sources |
|---|---|---|
| Quercetin | Full SMILES string | Red Onions, Apples, Berries |
| EGCG | Full SMILES string | Green Tea |
| Curcumin | Full SMILES string | Turmeric |
| Allicin | `C=CCS(=O)SCC=C` | Garlic |
| Resveratrol | Full SMILES string | Red Grapes, Peanuts |
| Sulforaphane | `CS(=O)CCCCN=C=S` | Broccoli, Brussels Sprouts |
| Gingerol | Full SMILES string | Ginger |
| Lycopene | Full SMILES string | Tomatoes |
| Capsaicin | Full SMILES string | Chili Peppers |
| Luteolin | Full SMILES string | Celery, Parsley |
| Apigenin | Full SMILES string | Chamomile, Celery |
| Naringenin | Full SMILES string | Grapefruit, Oranges |
| Kaempferol | Full SMILES string | Kale, Broccoli |
| Ellagic Acid | Full SMILES string | Pomegranate, Berries |
| Diallyl Disulfide | Full SMILES string | Garlic, Onions |

**Backend — `threat_backend/amina_ai.py` → `amina_analyse(sequence)`**
- Full pipeline: analyse_protein → score_compounds → FLock LLM enrichment
- Returns complete analysis with top compounds, nutrition strategy, and dietary advice

**Backend — `threat_backend/research_agent.py`** (for unknown diseases)
- When disease is not in `disease_nutrition_db.json`, uses FLock LLM to research nutrition strategies
- Receives WHO context (overview, advice, assessment, epidemiology) + optional Amina AI results
- Returns structured nutrition strategy with compounds, mechanisms, food sources

**Mapping Pipeline — `threat_backend/nutrient_mapper.py`**
- **Tier 1**: `data/disease_nutrition_db.json` — 11 diseases with evidence-based compound mappings (influenza, norovirus, RSV, COVID-19, measles, mpox, legionella, hay fever, E. coli, malaria, cholera)
- **Tier 2**: Research agent results — AI-generated strategies for unknown diseases
- **Tier 3**: Category-based fallback — 5 threat categories with default compounds

**Fallback Mock**
- Pre-computed docking results in `data/mock_docking/`
- If Amina AI fails for unknown diseases → research agent fallback → category fallback

---

## Phase 5: App Layer Integration & Proactive Alert (The Defense Protocol) — ✅ IMPLEMENTED

### Concept
The system **proactively** pushes threat alerts and adapted meal plans to users via webhooks — no user action needed. Layer 3 detects changes in threat data and fires webhooks to Layer 2, which maps cities to users, pushes formatted reports to Telegram, and auto-chains to the nutrition agent for meal adaptation.

### Inputs
- Threat change detection from Layer 3 (webhook payload with report text, active threats, priority foods)
- User's baseline data (macros, allergies, diet type) from local profile

### Outputs
- Proactive Telegram alert with full threat report
- Auto-adapted meal plan featuring biodefense foods
- On-demand threat report via `/threats` command

### Privacy: No User Data Leaves the Machine
The adaptation happens entirely locally:
1. Layer 3 fires webhook with public threat data only (city + threats + foods)
2. Layer 2 receives webhook, looks up users for that city in its local registry
3. Layer 2 pushes report to user's Telegram
4. Layer 2 auto-chains to Nutrition Agent with threat context → FLock API + user's LOCAL profile
5. Nutrition Agent returns adapted plan → sent to user

### Implementation

**Webhook Architecture (Layer 3 → Layer 2)**

Layer 3 (`threat_backend/server.py`):
- `_compute_threat_fingerprint(city_data)` → SHA-256 hash of threat names + severities
- `_fire_webhooks(changed_cities)` → POST to all subscriber callbacks for changed cities
- `_subscribers` registry: `{city: {callback_id: callback_url}}` — zero user data
- POST `/subscribe` → register callback URL for a city
- POST `/unsubscribe` → remove callback registration
- GET `/subscribers` → subscriber counts (admin/debug)
- First refresh is "initial load" — no webhooks. Changes detected from 2nd cycle onward.

Layer 2 (`agents/orchestrator.py`):
- `_start_webhook_receiver()` → FastAPI on port 8200
- `_handle_proactive_alert(payload)` → receives webhook, pushes to Telegram, chains to nutrition
- `_ensure_subscribed(user_id, city)` → auto-register with Layer 3
- `subscribe_on_profile_complete()` → hook called when onboarding finishes
- `_auto_subscribe_existing_profiles()` → startup re-registration
- Per-user registries: `_city_users` (city → set of chat_ids), `_user_cities` (chat_id → city)

**Proactive Alert Flow:**
```
Layer 3: Hourly refresh detects London threats changed
  → _fire_webhooks(["london"])
  → POST http://127.0.0.1:8200/threat-alert
    { event: "threat_alert", city: "London",
      report_text: "🛡 Threat Report...",
      active_threats: [...], priority_foods: [...] }

Layer 2: _handle_proactive_alert()
  → Look up users: _city_users["london"] = {"7599032986"}
  → Push to Telegram: "🔔 Proactive Threat Alert\n\n" + report_text
  → Auto-chain: nutrition_process(user_id, "[CHAIN_CONTEXT:...] adapt meals")
  → Push adapted plan: "🍽 Auto-Adapted Meal Plan\n\n" + adapted_reply
```

**On-Demand Flow (`/threats` command):**
```
User: /threats
  → _handle_threat(user_id)
  → _ensure_subscribed(user_id, city)
  → GET /threats/{city}/report → returns pre-formatted text + chain context
  → Forward report to user
  → If threats: chain to nutrition agent with boost nutrients
```

**Auto-Subscribe on Profile Complete:**
```
User completes onboarding
  → Orchestrator detects city in profile
  → _ensure_subscribed(user_id, city)
  → POST /subscribe to Layer 3
  → User now automatically receives proactive alerts
```

**FLock Alliance — Federated Learning (Planned)**
- Local training of efficacy signals → weight-only export to FLock Alliance
- Privacy-preserving: server never sees individual user data
- Planned for future implementation

---

## API Endpoints Summary (Threat Intelligence Backend — Zero-Knowledge, Port 8100)

All endpoints serve **public threat data only**. No user PII is received or stored.

| Method | Path | Phase | Description |
|--------|------|-------|-------------|
| `GET` | `/health` | 2 | System status + subscriber count + last refresh time |
| `GET` | `/threats/{city}` | 2 | Full threat JSON for a city (AQI + outbreaks + nutrients + foods + sequences + Amina) |
| `GET` | `/threats/{city}/report` | 5 | Pre-formatted Telegram report text + chain context for meal adaptation |
| `GET` | `/threats` | 2 | All cities summary (sorted by threat count) |
| `GET` | `/nutrients/{city}` | 4 | Nutrient recommendations only (lightweight) |
| `GET` | `/cities` | 2 | All 25 monitored UK cities |
| `POST` | `/subscribe` | 5 | Register anonymous callback URL for city webhook alerts |
| `POST` | `/unsubscribe` | 5 | Remove callback registration |
| `GET` | `/subscribers` | 5 | Subscriber counts per city (admin/debug) |

**Removed endpoints (privacy):** No `/api/users/*` endpoints. User data never reaches the server. Onboarding, meal plans, meal logging, and profile management all happen locally on the user's machine.

---

## Background Scheduler (In-Process)

The threat backend uses an in-process asyncio background task (no Celery/Redis needed).

| Task | Frequency | Description |
|------|-----------|-------------|
| `refresh_all_cities()` | Every 1 hour | WHO DON fetch → NCBI sequences → Amina AI/Research → 25 city refresh → change detection → webhook fire |

---

## Environment Variables (`.env`)

```bash
# ── FLock API (LLM Brain for all agents) ──
FLOCK_API_KEY=sk-...                    # Get key at: https://platform.flock.io
FLOCK_BASE_URL=https://api.flock.io/v1  # OpenAI-compatible endpoint
FLOCK_MODEL=qwen3-30b-a3b-instruct-2507

# ── Telegram Bot ──
TELEGRAM_BOT_TOKEN=...                  # Bot token from @BotFather

# ── Optional — AQI (leave blank to use mock data) ──
OWM_API_KEY=                            # OpenWeatherMap API key

# ── Backend URLs (defaults work for local dev) ──
THREAT_BACKEND_URL=http://127.0.0.1:8100
WEBHOOK_RECEIVER_PORT=8200
```

---

## Running the System

**Two terminals required:**

```bash
# Terminal 1: Start the threat backend (Layer 3)
python -m threat_backend
# → FastAPI on localhost:8100
# → Fetches WHO DON + NCBI + Amina AI on startup
# → Refreshes all 25 cities every hour
# → Fires webhooks when threats change

# Terminal 2: Start the bot (Layer 2)
python -m agents.orchestrator
# → Connects to Telegram (polling)
# → Starts webhook receiver on localhost:8200
# → Auto-subscribes existing user profiles to Layer 3
# → Receives proactive alerts, pushes to Telegram
# → Auto-chains to nutrition agent for meal adaptation
```

---

## Verification & Testing

- **Unit tests:** TDEE calculation, validators, nutrient mapper, phytochemical JSON loading
- **Privacy test:** Inspect all outbound HTTP calls — verify ZERO user PII leaves the local machine. Only city names and anonymous callback URLs are sent to Layer 3.
- **Integration test:** Full pipeline — WHO DON fetch → NCBI sequence → Amina AI analysis → nutrient mapping → report generation → webhook fire → Telegram alert
- **Chat demo flow:** Onboard a user via Telegram → set city to London → `/threats` command → see real WHO outbreak data + NCBI protein data + Amina AI analysis + adapted meal plan
- **Proactive alert test:** Wait for hourly refresh → observe webhook fire for changed cities → confirm Telegram push to subscribed users
- **Fallback test:** Block WHO API → verify system falls back to mock outbreaks. Block NCBI → verify system continues without protein data.

---

## Key Decisions

| Decision | Chosen | Over | Reason |
|----------|--------|------|--------|
| **Privacy model** | **Local-first, zero-knowledge backend** | Centralized user storage | User health data is confidential — never leaves the user's machine |
| Chat Gateway | OpenClaw (runs locally) + built-in Telegram polling | Custom bot code per platform | Native multi-channel support; MVP uses direct Telegram polling for simplicity |
| Agent Architecture | **Per-user personal agent + specialized handlers** | Separate agent classes per concern | Orchestrator routes intent; handlers for onboarding, nutrition, threats |
| LLM Brain | **FLock API** (`api.flock.io/v1`) | OpenAI / Anthropic / Ollama | Hackathon sponsor product, OpenAI-compatible, decentralized, lower cost |
| User data storage | Local JSON files (`data/profiles/`) | MongoDB / cloud DB | Simple, portable, privacy-first — one file per user |
| Threat data | **Real WHO DON + NCBI** | Mock data only | Real-world data from public APIs; mocks as fallback |
| Protein analysis | **Amina AI (local)** | Cloud ESMFold | No cloud compute needed; 8-factor scoring runs locally |
| Nutrient mapping | **3-tier hybrid** (disease_db → research → category) | Single-tier lookup | Best coverage: evidence-based for known diseases, AI for unknown, fallback for all |
| Alert model | **Webhook proactive push** | User-initiated polling only | Users get alerts without asking; Layer 3 fires on change |
| Backend role | Zero-knowledge threat API + webhook server | Full-stack API | Backend serves anonymous public data + anonymous callbacks, never sees user PII |
| Database | **In-memory cache** | MongoDB / PostgreSQL | Simpler for MVP; no external DB dependency; refreshes hourly |
| Background tasks | **In-process asyncio** | Celery + Redis | No external worker infrastructure needed; single process |
| Language | Python (FastAPI + agents) | Node.js | Best bioinformatics ecosystem, agent logic in Python |
| Agent fallback | Step-by-step mode when FLock API unavailable | Hard failure | Graceful degradation — bot still works without LLM, just less natural |