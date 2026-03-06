# Threat Detection System — Design Document

> **Last Updated**: March 2026 — v2.0 (Webhook Architecture)

## Overview

The Threat Detection system is a fully autonomous pipeline that monitors environmental and health risks across 25 UK cities and maps each threat to protective nutrition recommendations. When threats **change**, it proactively pushes alerts to users via webhooks — users don't need to ask.

**Three key innovations since v1:**
1. **Real WHO Disease Outbreak News** (OData API) — replaced mock outbreaks with live WHO intelligence
2. **Amina AI Protein Analysis** — amino acid sequence analysis scoring phytochemicals against pathogen proteins
3. **Webhook-driven Proactive Alerts** — Layer 3 detects changes and pushes to Layer 2 automatically

**Two layers, clear separation:**

| Component | Layer | Location | Role |
|-----------|-------|----------|------|
| Threat Intelligence Backend | Layer 3 (System) | `threat_backend/` | Always-on FastAPI service. Fetches, caches, analyses, and serves threat data. Fires webhooks when threats change. **Zero-knowledge** — never sees user data. |
| Personal Agent Orchestrator | Layer 2 (App) | `agents/orchestrator.py` | Per-user personal agent. Subscribes to Layer 3 for user's city. Receives proactive alerts. Pushes reports to Telegram. Auto-chains to meal adaptation. |

**Key principle:** Layer 3 is shared infrastructure (one instance serves all). Layer 2 is personal (one logical instance per user). Layer 3 never knows WHO subscribes — only that someone cares about a city.

---

## System Architecture — Overview

```
  ┌───────────────────────────────────────────────────────────────────┐
  │                    USER (Telegram / Discord)                      │
  └──────────────────────────┬──────────────────▲─────────────────────┘
                             │ /threats          │ 🔔 Proactive Alert
                             ▼                   │
  ┌──────────────────────────────────────────────────────────────────┐
  │  Layer 2: Personal Agent Orchestrator   (agents/orchestrator.py) │
  │  + Webhook Receiver on port 8200                                 │
  │                                                                  │
  │  ON-DEMAND (/threats command):                                   │
  │  1. detect_intent() → "threat"                                   │
  │  2. load user profile → get city                                 │
  │  3. _ensure_subscribed(user_id, city) → POST /subscribe          │
  │  4. GET http://localhost:8100/threats/{city}/report               │
  │  5. Forward pre-formatted report to user                         │
  │  6. If threats found → auto-chain to nutrition agent             │
  │                                                                  │
  │  PROACTIVE (webhook from Layer 3):                               │
  │  1. POST /threat-alert received on :8200                         │
  │  2. Look up users in that city (_city_users registry)            │
  │  3. Push report to each user's Telegram                          │
  │  4. Auto-chain → nutrition agent adapts meal plan                │
  │  5. Push adapted meal plan to user                               │
  └──────────────────────────┬──────────▲────────────────────────────┘
                             │          │ POST /threat-alert
              POST /subscribe│          │ (webhook callback)
         GET /threats/{city} │          │
                /report      │          │
                             ▼          │
  ┌──────────────────────────────────────────────────────────────────┐
  │  Layer 3: Threat Intelligence Backend    (threat_backend/)       │
  │  FastAPI on localhost:8100                                       │
  │                                                                  │
  │  ┌─────────────────────────────────────────────────────────┐     │
  │  │  Background Scheduler (every 1 hour)                    │     │
  │  │                                                         │     │
  │  │  Step 1: Fetch WHO DON outbreaks (OData API)            │     │
  │  │  Step 2: Fetch NCBI protein sequences (Entrez)          │     │
  │  │  Step 3: Amina AI + Research Agent (unknown diseases)   │     │
  │  │  Step 4: For each of 25 cities:                         │     │
  │  │    • Fetch AQI data (OpenWeatherMap or mock)            │     │
  │  │    • Generate outbreaks (WHO + city context)            │     │
  │  │    • Map threats → nutrients (3-tier hybrid pipeline)   │     │
  │  │    • Compute threat fingerprint (for change detection)  │     │
  │  │    • Generate formatted report text                     │     │
  │  │    • Store in memory cache                              │     │
  │  │  Step 5: Diff fingerprints vs last cycle                │     │
  │  │    • If threats changed → fire webhooks to subscribers  │     │
  │  └─────────────────────────────────────────────────────────┘     │
  │                                                                  │
  │  Subscriber Registry:                                            │
  │    _subscribers: {city_lower: {callback_id: callback_url}}       │
  │    Zero user data — only anonymous callback URLs per city        │
  │                                                                  │
  │  API Endpoints:                                                  │
  │    GET  /health               → system status + subscriber count │
  │    GET  /threats/{city}       → full threat JSON                 │
  │    GET  /threats/{city}/report→ pre-formatted report + chain ctx │
  │    GET  /threats              → all cities summary               │
  │    GET  /nutrients/{city}     → nutrient recs only (lightweight) │
  │    GET  /cities               → list of 25 monitored cities     │
  │    POST /subscribe            → register city webhook callback   │
  │    POST /unsubscribe          → remove callback registration     │
  │    GET  /subscribers          → subscriber counts (admin/debug)  │
  └──────────────────────────────────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────────┐
         ▼                   ▼                       ▼
  ┌─────────────┐   ┌───────────────┐   ┌───────────────────────┐
  │ WHO DON     │   │ NCBI Entrez   │   │ OpenWeatherMap API    │
  │ OData API   │   │ Protein DB    │   │ (real AQI data)       │
  │ (outbreaks) │   │ (sequences)   │   │ Free tier, 1K/day     │
  └─────────────┘   └───────────────┘   └───────────────────────┘
         │
         ▼
  ┌───────────────────────────────────────────────┐
  │  Local Data Sources                           │
  │  • disease_nutrition_db.json (11 diseases)    │
  │  • phytochemicals.json (15 compounds + foods) │
  └───────────────────────────────────────────────┘
```

---

## Detailed Architecture — File-by-File

### Backend Files (`threat_backend/`)

```
threat_backend/
├── __init__.py              # Package marker
├── __main__.py              # Entry: python -m threat_backend → uvicorn on :8100
├── server.py                # FastAPI app + scheduler + endpoints + subscriber registry
│                            #   + report formatter + change detection + webhook fire
├── cities.py                # 25 UK cities with lat/lon coordinates
├── aqi_fetcher.py           # AQI data: real OpenWeatherMap API or mock fallback
├── outbreak_fetcher.py      # WHO Disease Outbreak News (OData) + 3-tier filtering
├── outbreak_mock.py         # Legacy mock fallback (still available)
├── sequence_fetcher.py      # NCBI Entrez protein sequence fetcher + cache
├── amina_ai.py              # Amino-acid Intelligence for Nutritional Antagonists
│                            #   Protein analysis + compound scoring + LLM enrichment
├── research_agent.py        # FLock LLM research for unknown diseases
└── nutrient_mapper.py       # 3-tier hybrid mapping: disease_db → research → category
```

### Orchestrator Integration (`agents/orchestrator.py`)

```
_handle_threat()             # Slimmed: calls /threats/{city}/report, forwards text
_ensure_subscribed()         # Auto-subscribes user's city to Layer 3
_handle_proactive_alert()    # Receives webhook, pushes to Telegram, chains to nutrition
_start_webhook_receiver()    # FastAPI on :8200 for Layer 3 callbacks
subscribe_on_profile_complete()  # Hook: called when onboarding sets city
_auto_subscribe_existing_profiles()  # Startup: re-register all profiles
_threat_cb                   # Circuit breaker (3 failures → 30s cooldown)
_city_users                  # city_lower → set of chat_ids (personal mapping)
_user_cities                 # chat_id → city_lower (for unsub on city change)
_subscribed_cities           # set of cities subscribed to Layer 3
```

---

## Data Flow — Step by Step

### Flow 1: Hourly Background Refresh (Layer 3)

This runs autonomously, no user involved.

```
┌─────────────────────────────────────────────────────────────┐
│  server.py → _refresh_loop()                                │
│                                                             │
│  Every 3600 seconds:                                        │
│                                                             │
│  ── Step 1: WHO Outbreaks ──                                │
│  outbreak_fetcher.generate_outbreaks_from_who()             │
│    ├─ Fetch WHO DON OData API (public, no auth)             │
│    │   URL: who.int/api/news/diseaseoutbreaknews            │
│    ├─ 3-tier geographic filtering:                          │
│    │   Tier 1 (UK) → severity "high"                        │
│    │   Tier 2 (EURO) → severity "moderate"                  │
│    │   Tier 3 (Global) → severity "low"                     │
│    ├─ Extract disease key with extract_disease_key()        │
│    ├─ Strip HTML from WHO content fields                    │
│    └─ Enrich each outbreak with WHO advice/overview          │
│                                                             │
│  ── Step 2: NCBI Protein Sequences ──                       │
│  sequence_fetcher.fetch_sequences_for_outbreaks()           │
│    ├─ For each detected disease in disease_nutrition_db     │
│    ├─ NCBI Entrez esearch → efetch protein sequences        │
│    ├─ Cache results (avoid redundant fetches)               │
│    └─ Returns: {disease_key: {protein_id, title,            │
│                               organism, length, sequence}}  │
│                                                             │
│  ── Step 3: Amina AI + Research (Unknown Diseases) ──       │
│  For each outbreak where extract_disease_key() = "unknown": │
│    ├─ 3a: NCBI fetch for unknown disease protein            │
│    │       e.g. "Chikungunya virus protein"                 │
│    ├─ 3b: amina_ai.amina_analyse(sequence)                  │
│    │       ├─ analyse_protein() → composition, motifs,      │
│    │       │   binding opportunities, structural hints      │
│    │       ├─ score_compounds_against_protein() →            │
│    │       │   8-factor scoring of 15 phytochemicals         │
│    │       └─ FLock LLM enrichment → nutrition strategy     │
│    └─ 3c: If Amina fails → research_agent fallback          │
│           research_unknown_disease() → FLock LLM strategy   │
│                                                             │
│  ── Step 4: Refresh Each City ──                            │
│  For each of 25 cities:                                     │
│    ├─ fetch_aqi(lat, lon, city) → AQI + components          │
│    ├─ generate_outbreaks_from_who(city, country)            │
│    ├─ Combine AQI threats + outbreak threats                │
│    ├─ nutrient_mapper.map_all_threats() → 3-tier hybrid:    │
│    │   1st: disease_nutrition_db.json (11 diseases)         │
│    │   2nd: research_agent results (unknown diseases)       │
│    │   3rd: category-based fallback (5 categories)          │
│    ├─ get_priority_foods() → top 5 ranked foods             │
│    ├─ format_threat_report() → Telegram-ready text          │
│    ├─ _compute_threat_fingerprint() → SHA-256 hash          │
│    └─ Compare fingerprint vs last cycle → detect changes    │
│                                                             │
│  ── Step 5: Fire Webhooks ──                                │
│  _fire_webhooks(changed_cities)                             │
│    ├─ For each city with changed threats:                   │
│    │   For each subscriber callback URL:                    │
│    │     POST {event, city, report_text, active_threats,    │
│    │           priority_foods, nutrient_recommendations,    │
│    │           timestamp}                                   │
│    └─ Log delivery results                                  │
│                                                             │
│  Note: First refresh is "initial load" — no webhooks fired. │
│  Changes detected from 2nd refresh onward.                  │
└─────────────────────────────────────────────────────────────┘
```

### Flow 2: User Asks `/threats` (On-Demand)

```
User sends: /threats
     │
     ▼
Orchestrator.detect_intent()
     │ slash command "/threats" → maps to "threat" intent
     ▼
Orchestrator._dispatch()
     │ intent == "threat"
     ▼
Orchestrator._handle_threat(user_id, text)
     │
     ├─ 1. load_profile(user_id) → get city (e.g., "London")
     │
     ├─ 2. _ensure_subscribed(user_id, city)
     │      → Register callback with Layer 3 if not already
     │      → POST /subscribe {city: "London",
     │                         callback_url: "http://127.0.0.1:8200/threat-alert"}
     │      → Track user_id → city in _city_users + _user_cities
     │
     ├─ 3. GET http://localhost:8100/threats/london/report
     │      → Layer 3 returns:
     │        { report_text: "🛡 Threat Report: London\n...",
     │          threat_count: 3,
     │          chain_context: {threat_type, recommendation, boost_nutrients},
     │          last_updated: "2026-03-05T..." }
     │
     ├─ 4. Forward report_text directly to user (already formatted)
     │
     └─ 5. If chain_context exists:
            → Build chain: {"to": "meal_adapt", "context": chain_context}
            → Orchestrator chains to nutrition agent
            → Nutrition agent adapts meal plan with threat context
```

### Flow 3: Proactive Alert (Webhook — No User Action)

```
Layer 3 hourly refresh detects: London threats changed
     │
     ├─ Fingerprint differs from last cycle
     ▼
Layer 3: _fire_webhooks(["london"])
     │
     ├─ Look up subscribers for "london"
     │   _subscribers["london"] = {"abc123": "http://127.0.0.1:8200/threat-alert"}
     │
     ├─ POST http://127.0.0.1:8200/threat-alert
     │   Body: { event: "threat_alert",
     │           city: "London",
     │           report_text: "🛡 Threat Report: London\n...",
     │           active_threats: [...],
     │           priority_foods: [...],
     │           nutrient_recommendations: [...],
     │           timestamp: "2026-03-05T..." }
     ▼
Layer 2: _handle_proactive_alert(payload)
     │
     ├─ 1. Look up users in London:
     │      _city_users["london"] = {"7599032986"}
     │
     ├─ 2. For each user:
     │      send_telegram(chat_id=7599032986,
     │        text="🔔 *Proactive Threat Alert*\n\n" + report_text)
     │
     └─ 3. Auto-chain meal adaptation:
            nutrition_process(user_id,
              "[CHAIN_CONTEXT:{threat_type, boost_nutrients}] adapt meals")
            → Send adapted plan:
              "🍽 *Auto-Adapted Meal Plan*\n\n" + adapted_reply
```

### Flow 4: Auto-Subscribe on Profile Complete

```
User completes onboarding → says "yes" to confirm
     │
     ▼
Orchestrator._dispatch("onboarding", ...)
     │
     ├─ onboarding_process() → saves profile with city="London"
     │
     └─ After reply returned:
          load_profile(user_id) → has city?
          → YES: _ensure_subscribed(user_id, "London")
                 → POST /subscribe to Layer 3
                 → User now receives proactive alerts automatically
```

### Flow 5: City Change → Resubscribe

```
User: "update my city to Manchester"
     │
     ▼
_ensure_subscribed(user_id, "manchester")
     │
     ├─ Old city = "london"
     ├─ Remove user from _city_users["london"]
     ├─ If no users left for london → POST /unsubscribe
     ├─ Add user to _city_users["manchester"]
     └─ POST /subscribe for "manchester" if not yet subscribed
```

---

## Component Details

### 1. cities.py — City Registry

**Purpose**: Single source of truth for monitored locations.

**25 cities:**
- **England (20)**: London, Birmingham, Manchester, Leeds, Liverpool, Sheffield, Bristol, Newcastle, Nottingham, Leicester, Coventry, Bradford, Southampton, Brighton, Plymouth, Wolverhampton, Reading, Derby, Sunderland, Norwich
- **Scotland (5)**: Edinburgh, Glasgow, Aberdeen, Dundee, Inverness

**Functions**:
| Function | Input | Output |
|----------|-------|--------|
| `find_city(query)` | Any string | Matching city dict or None (case-insensitive, partial match) |
| `all_city_names()` | — | List of 25 city name strings |
| `CITY_LOOKUP` | — | Dict: lowered name → city dict |

---

### 2. aqi_fetcher.py — Air Quality Data

**Two modes:**

| Mode | Trigger | Source |
|------|---------|--------|
| Real | `OWM_API_KEY` set | OpenWeatherMap Air Pollution API |
| Mock | No API key | Deterministic, city-aware simulation |

**AQI Scale**:
| Index | Label | Is Threat? |
|-------|-------|------------|
| 1 | Good | No |
| 2 | Fair | No |
| 3 | Moderate | Yes |
| 4 | Poor | Yes |
| 5 | Very Poor | Yes |

---

### 3. outbreak_fetcher.py — WHO Disease Outbreak News (Real Data)

**Purpose**: Fetch real WHO DON outbreak data via OData API.

**API**: `https://www.who.int/api/news/diseaseoutbreaknews` — public, no auth needed.

**3-tier geographic filtering:**

| Tier | Filter | Severity | Example |
|------|--------|----------|---------|
| Tier 1 (UK) | Region = United Kingdom | high | "Measles - United Kingdom" |
| Tier 2 (EURO) | Region = European Region | moderate | "Avian Influenza - France" |
| Tier 3 (Global) | All WHO DON items | low | "H5N1 - Egypt" |

**Processing pipeline:**
1. Fetch OData JSON (cached for 6 hours)
2. Filter by tier using WHO region GUIDs
3. Strip HTML from `OverviewSection`, `Advice`, `Assessment`, `Epidemiology`
4. Extract disease key via `extract_disease_key()` for nutrition DB lookup
5. Return enriched outbreak dicts with WHO context fields

---

### 4. sequence_fetcher.py — NCBI Protein Sequences

**Purpose**: Fetch amino acid sequences for detected pathogens from NCBI GenBank.

**API**: NCBI Entrez (`esearch` + `efetch`) — public, rate-limited.

**Cache**: In-memory, keyed by disease name. Avoids redundant fetches across refresh cycles.

**Output per disease:**
```json
{
  "protein_id": "ABC12345.1",
  "title": "hemagglutinin [Influenza A virus (H5N1)]",
  "organism": "Influenza A virus",
  "length": 561,
  "sequence": "MEKIVLLLAIVSLVKS..."
}
```

---

### 5. amina_ai.py — Amino-acid Intelligence for Nutritional Antagonists

**Purpose**: Analyse pathogen protein structure and score food compounds against it.

**Pipeline**:
1. `analyse_protein(sequence)` → amino acid composition, 15 pathogen motifs, binding opportunities, structural hints
2. `score_compounds_against_protein(analysis)` → 8-factor scoring of 15 phytochemicals:
   - `charge_complementarity` — electrostatic match
   - `hydrophobic_match` — hydrophobic surface interaction
   - `size_fit` — molecular weight vs binding pocket
   - `motif_relevance` — targets detected pathogen motifs
   - `aromatic_stacking` — pi-pi interaction potential
   - `hbond_potential` — hydrogen bonding capacity
   - `flexibility_match` — conformational adaptability
   - `literature_boost` — known antiviral/antibacterial evidence
3. `amina_analyse()` → full pipeline with FLock LLM enrichment → nutrition strategy

**15 compounds scored**: Quercetin, EGCG, Curcumin, Allicin, Resveratrol, Sulforaphane, Gingerol, Lycopene, Capsaicin, Luteolin, Apigenin, Naringenin, Kaempferol, Ellagic Acid, Diallyl Disulfide

---

### 6. research_agent.py — LLM Disease Research

**Purpose**: FLock LLM-powered research for diseases not in the nutrition database.

**Inputs**: Disease title, WHO context (overview, advice, assessment, epidemiology), optional protein sequence data, optional Amina AI results.

**Output**: Structured nutrition strategy with compounds, mechanisms, food sources, advice.

---

### 7. nutrient_mapper.py — 3-Tier Hybrid Mapping

**Purpose**: Map threats to protective nutrition via three levels of intelligence.

**Tier 1**: `disease_nutrition_db.json` — curated evidence-based data for 11 known diseases (influenza, norovirus, RSV, COVID-19, measles, mpox, legionella, hay fever, E. coli, malaria, cholera). Each entry has primary goal, key compounds with mechanisms, additional nutrients, and dietary advice.

**Tier 2**: Research agent results — AI-generated nutrition strategies for unknown diseases. Uses FLock LLM + Amina AI protein analysis.

**Tier 3**: Category-based fallback — 5 threat categories with default compound recommendations:
- `air_quality` → Quercetin, EGCG, Sulforaphane, Lycopene, Curcumin, Resveratrol
- `respiratory_virus` → Quercetin, Allicin, Gingerol, EGCG, Curcumin, Ellagic Acid
- `gi_pathogen` → Allicin, Gingerol, EGCG, Capsaicin, Curcumin
- `allergen` → Quercetin, Luteolin, Apigenin, Naringenin, Kaempferol
- `bacteria` → Allicin, EGCG, Curcumin, Ellagic Acid, Diallyl Disulfide

---

### 8. server.py — FastAPI Backend (Layer 3)

**Purpose**: Ties everything together. Scheduler, cache, endpoints, subscriber registry, change detection, webhook fire, report formatter.

**Key components:**

| Component | Purpose |
|-----------|---------|
| `refresh_all_cities()` | 5-step pipeline: WHO → NCBI → Amina/Research → cities → webhooks |
| `_refresh_loop()` | Background loop every 3600s |
| `format_threat_report()` | Generates Telegram-ready report text (moved from orchestrator) |
| `_compute_threat_fingerprint()` | SHA-256 hash of active threat names + severities |
| `_fire_webhooks()` | POST to subscriber callbacks for changed cities |
| `_subscribers` | `{city: {callback_id: callback_url}}` — zero user data |
| `_cache` | `{city: full_data}` — in-memory |
| `_report_cache` | `{city: formatted_text}` — pre-generated reports |
| `_threat_fingerprints` | `{city: hash}` — for change detection |

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | System status + subscriber count |
| GET | `/threats/{city}` | Full threat JSON for API consumers |
| GET | `/threats/{city}/report` | Pre-formatted text + chain context for meal adapt |
| GET | `/threats` | All cities summary (sorted by threat count) |
| GET | `/nutrients/{city}` | Nutrient recs only (lightweight) |
| GET | `/cities` | All 25 monitored cities |
| POST | `/subscribe` | Register anonymous callback URL for city |
| POST | `/unsubscribe` | Remove callback registration |
| GET | `/subscribers` | Subscriber counts per city (admin/debug) |

**Cache structure** (per city):
```json
{
  "city": "London",
  "country": "England",
  "aqi": { "aqi_index": 4, "aqi_label": "Poor", "components": {...} },
  "outbreaks": [ { "name": "...", "severity": "...", "source": "who_don_tier1", ... } ],
  "active_threats": [ ... ],
  "threat_count": 3,
  "nutrient_recommendations": [ { "mapping_source": "disease_db", ... } ],
  "priority_foods": [ { "food": "Green Tea", "serving": "1 cup", "compounds": [...] } ],
  "sequences": { "influenza": { "protein_id": "...", "title": "...", "length": 561 } },
  "amina_analyses": { "chikungunya": { "amina_summary": "...", "top_compounds": [...] } },
  "last_updated": "2026-03-05T01:19:43Z"
}
```

---

## Privacy Model

```
┌──────────────────────────┐          ┌──────────────────────────┐
│  Layer 2 (Personal)      │          │  Layer 3 (Shared)        │
│                          │          │                          │
│  Knows:                  │  HTTP    │  Knows:                  │
│  - user_id (chat_id)     │ ───────→ │  - city name             │
│  - full profile          │  city +  │  - AQI data              │
│  - which city → which    │  callback│  - WHO outbreak data     │
│    user (personal map)   │  URL     │  - NCBI protein data     │
│                          │          │  - Amina AI analyses     │
│  Sends to backend:       │  ←────── │  - nutrient mappings     │
│  ONLY city name +        │  JSON +  │  - callback URLs         │
│  anonymous callback URL  │  webhook │                          │
│                          │          │  Does NOT know:          │
│                          │          │  - who subscribes        │
│                          │          │  - any user data         │
│                          │          │  - any chat history      │
└──────────────────────────┘          └──────────────────────────┘
```

Layer 3 is a pure `city → threats` + `city → callbacks` function. It cannot identify users.

---

## Webhook Architecture Detail

### Subscribe Flow
```
Layer 2                              Layer 3
  │                                    │
  │  POST /subscribe                   │
  │  { city: "london",                 │
  │    callback_url:                   │
  │    "http://127.0.0.1:8200/        │
  │     threat-alert" }                │
  │ ──────────────────────────────→    │
  │                                    │  _subscribers["london"]["abc123"]
  │                                    │    = "http://127.0.0.1:8200/threat-alert"
  │  ←────────────────────────────     │
  │  { status: "subscribed",           │
  │    callback_id: "abc123" }         │
```

### Webhook Fire Flow
```
Layer 3 (after refresh)              Layer 2 (webhook receiver)
  │                                    │
  │  Fingerprint changed for london    │
  │                                    │
  │  POST http://127.0.0.1:8200/      │
  │       threat-alert                 │
  │  { event: "threat_alert",          │
  │    city: "London",                 │
  │    report_text: "🛡 Threat...",    │
  │    active_threats: [...],          │
  │    priority_foods: [...],          │
  │    timestamp: "..." }              │
  │ ──────────────────────────────→    │
  │                                    │  _handle_proactive_alert()
  │                                    │  → look up users in London
  │                                    │  → push to Telegram
  │                                    │  → auto-chain meal adapt
  │  ←────────────────────────────     │
  │  { status: "accepted" }            │
```

---

## Running the System

**Two terminals required**:

```bash
# Terminal 1: Start the threat backend (Layer 3)
python -m threat_backend
# → Runs on localhost:8100
# → Fetches all 25 cities on startup (WHO + NCBI + Amina AI)
# → Refreshes every hour
# → Fires webhooks when threats change

# Terminal 2: Start the bot (Layer 2)
python -m agents.orchestrator
# → Connects to Telegram (polling)
# → Starts webhook receiver on localhost:8200
# → Auto-subscribes existing profiles to Layer 3
# → Receives proactive alerts + pushes to users
```

**Environment variables** (in `.env`):
```bash
# FLock API (LLM brain)
FLOCK_API_KEY=sk-...
FLOCK_BASE_URL=https://api.flock.io/v1
FLOCK_MODEL=qwen3-30b-a3b-instruct-2507

# Telegram
TELEGRAM_BOT_TOKEN=...

# Optional — leave blank to use mock AQI data
OWM_API_KEY=

# Backend URLs (defaults work for local dev)
THREAT_BACKEND_URL=http://127.0.0.1:8100
WEBHOOK_RECEIVER_PORT=8200
```

---

## Change Detection Algorithm

```
_compute_threat_fingerprint(city_data):
  threats = city_data["active_threats"]
  names = sorted(t["name"] for t in threats)
  severity = sorted(t["severity"] for t in threats)
  raw = "|".join(names) + "||" + "|".join(severity)
  return SHA256(raw)[:16]

On each refresh cycle:
  new_fp = fingerprint(current_data)
  old_fp = _threat_fingerprints[city]
  if new_fp != old_fp AND has_threats AND refresh_count > 0:
    → City has CHANGED → add to webhook fire list
  _threat_fingerprints[city] = new_fp
```

First refresh is always "initial load" — no webhooks fired. Changes detected from 2nd cycle onward.

---

## Report Format (What Users See)

Generated by `format_threat_report()` in Layer 3, pushed to users:

```
🛡 Threat Report: London

🔴 Air Quality: Poor (4/5)
  PM2.5: 65.2 µg/m³

🧬 Active Monitoring:
  🟠 Influenza A(H5N1) - France (moderate) [WHO-EURO]
    Avian influenza A(H5N1) human case reported in France
    📋 WHO Advice: Thorough cooking of poultry products...
  🔴 Measles - United Kingdom (high) [WHO-UK]
    Ongoing measles transmission in England
    📋 WHO Advice: Vaccination is the most effective...

⚠️ 3 active threat(s) detected

🔬 Disease-Specific Nutrition Intelligence:
  📚 Evidence-based — Influenza
  🎯 Goal: Support immune response, reduce viral replication
    • Quercetin: Inhibits neuraminidase → reduces viral entry
    • EGCG: Blocks hemagglutinin binding → prevents attachment
    • Allicin: Broad-spectrum antimicrobial activity
  ➕ Also important: Vitamin C, Zinc, Vitamin D

🍽 Top Foods to Eat Now:
  • Green Tea (1 cup brewed)
    Contains: EGCG, Quercetin
  • Turmeric (1 tsp ground)
    Contains: Curcumin
  • Red Onions (1 medium onion)
    Contains: Quercetin

🧪 Pathogen Protein Data (NCBI):
  • influenza: hemagglutinin [Influenza A virus] (561 aa)

🧬 Amina AI Protein Analysis:
  High hydrophobic content (45.2%) suggests membrane-active protein
  🎯 Top compounds: Curcumin, Resveratrol, Gingerol
```

---

## Future Improvements

| Feature | Status | Notes |
|---------|--------|-------|
| Real AQI via OpenWeatherMap | Ready | Just add `OWM_API_KEY` to `.env` |
| WHO DON real outbreaks | ✅ Done | Live OData API with 3-tier filtering |
| NCBI protein sequences | ✅ Done | Entrez esearch + efetch |
| Amina AI protein analysis | ✅ Done | 8-factor compound scoring |
| Research agent (unknown diseases) | ✅ Done | FLock LLM with Amina enrichment |
| Disease nutrition DB (11 diseases) | ✅ Done | Evidence-based compound mappings |
| Webhook proactive alerts | ✅ Done | Layer 3 → Layer 2 on threat change |
| Per-user personal agents | ✅ Done | One logical instance per chat_id |
| Auto-subscribe on profile complete | ✅ Done | Onboarding hook |
| Auto-chain meal adaptation | ✅ Done | Proactive alert → adapt meals |
| Persistent cache (survive restarts) | Planned | Write to disk on each refresh |
| Multi-region (beyond UK) | Planned | Extend cities.py |
| ESMFold 3D protein structure | Planned | Phase 3 from spec |
| DiffDock molecular docking | Planned | Phase 4 from spec |
