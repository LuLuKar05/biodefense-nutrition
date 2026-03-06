USER (any channel)
  │
  │  "Hey I'm Sarah, 28, trying to lose weight, I live in London"
  │
  ▼
┌─────────────────────────────────────────────────────────────────┐
│                     OPENCLAW GATEWAY                             │
│                     (runs locally on user's machine)             │
│                                                                  │
│   Channels connected:                                            │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│   │ Telegram │ │ Discord  │ │ WhatsApp │ │ WebChat  │  ...      │
│   └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘          │
│        │            │            │            │                   │
│        └────────────┴────────────┴────────────┘                  │
│                          │                                       │
│                 Unified message format:                           │
│                 {                                                 │
│                   "text": "Hey I'm Sarah...",                    │
│                   "channel": "telegram",                         │
│                   "user_id": "12345",                            │
│                   "chat_id": "67890"                             │
│                 }                                                 │
│                          │                                       │
└──────────────────────────┼───────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                 PERSONAL AGENT ORCHESTRATOR                       │
│                 agents/orchestrator.py                            │
│                 (runs locally — per-user personal agent)          │
│                                                                  │
│   Step 1: Is onboarding complete?                                │
│           NO → route to Onboarding Agent                         │
│           YES → detect intent → route to correct handler         │
│                                                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                   HANDLERS                               │   │
│   │                                                          │   │
│   │   ┌──────────────┐    Each handler calls FLock API      │   │
│   │   │  Onboarding  │    for natural language               │   │
│   │   │  Agent       │◄──►understanding + generation         │   │
│   │   └──────────────┘    + auto-subscribe on complete       │   │
│   │   ┌──────────────┐         ┌─────────────────────┐      │   │
│   │   │  Nutrition   │         │                     │      │   │
│   │   │  Agent       │◄───────►│  FLock API          │      │   │
│   │   └──────────────┘         │  api.flock.io/v1    │      │   │
│   │   ┌──────────────┐         │                     │      │   │
│   │   │  Threat      │         │  Model:             │      │   │
│   │   │  Handler     │◄─ GET ──│  qwen3-30b          │      │   │
│   │   │  (in orch.)  │  /report│                     │      │   │
│   │   └──────────────┘   from  │  Understands text,  │      │   │
│   │                    Layer 3 │  extracts data,     │      │   │
│   │                            │  generates replies  │      │   │
│   │                            └─────────────────────┘      │   │
│   │                                                          │   │
│   │   Each handler also uses local TOOLS:                    │   │
│   │   ┌──────────────────────────────────────────────┐      │   │
│   │   │  agents/tools/                                │      │   │
│   │   │  ├── macro_calculator.py   (TDEE, macros)    │      │   │
│   │   │  ├── meal_planner.py       (meal generation) │      │   │
│   │   │  ├── meal_manager.py       (meal logging)    │      │   │
│   │   │  ├── profile_manager.py    (profile CRUD)    │      │   │
│   │   │  ├── validators.py         (field validation)│      │   │
│   │   │  └── circuit_breaker.py    (fault tolerance)  │      │   │
│   │   └──────────────────────────────────────────────┘      │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │  WEBHOOK RECEIVER (port 8200)  ◄── Layer 3 webhook      │   │
│   │  POST /threat-alert                                      │   │
│   │  → Look up users in city                                 │   │
│   │  → Push report to Telegram                               │   │
│   │  → Auto-chain to nutrition agent for meal adaptation     │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│   Step 2: Handler returns reply text                             │
│                          │                                       │
└──────────────────────────┼───────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     OPENCLAW GATEWAY / TELEGRAM                  │
│                     (sends reply back to the SAME channel)       │
│                                                                  │
│   Reply → Telegram / Discord / WhatsApp / WebChat / ...         │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
                     USER sees reply

═══════════════════════════════════════════════════
PHASE 1: ONBOARDING (Onboarding Agent)
═══════════════════════════════════════════════════

User (Telegram): "Hey there!"
Orchestrator:     Profile not complete → route to Onboarding Agent
Onboarding Agent: Calls FLock → "Hi! I'm NutriShield 🛡️ ... what's your name?"
User:             "I'm Sarah, 28 years old"
Onboarding Agent: FLock extracts {name: Sarah, age: 28}
                  Validators: ✅ both valid
                  Saved 2/9 fields
                  FLock generates: "Nice to meet you Sarah! Are you male or female?"
User:             "Female, 65kg, 163cm, no allergies, love mediterranean food"
Onboarding Agent: FLock extracts 5 fields at once
                  Validators: ✅ all valid
                  Saved 7/9 fields
                  FLock: "Almost done! What's your goal and city?"
User:             "Trying to cut weight, I live in London"
Onboarding Agent: FLock extracts {goal: cut, city: London}
                  Validators: ✅ valid
                  Saved 9/9 ← COMPLETE
                  Shows summary → user confirms → profile saved locally
                  → AUTO-SUBSCRIBE: _ensure_subscribed(user_id, "london")
                    → POST /subscribe to Layer 3 for London
                    → User now receives proactive threat alerts

═══════════════════════════════════════════════════
PHASE 1.5: NUTRITION (Nutrition Agent)
═══════════════════════════════════════════════════

User (Discord):   "What should I eat today?"
Orchestrator:     Profile complete + intent=meal_plan → Nutrition Agent
Nutrition Agent:  macro_calculator.py → 1756 kcal, 176g protein...
                  meal_planner.py → mediterranean cut template
                  FLock generates natural meal plan reply
                  →  "Here's your plan for today! 🍽️
                      Breakfast: Greek yogurt with berries...
                      Lunch: Grilled chicken with quinoa salad..."

═══════════════════════════════════════════════════
PHASE 2: THREAT DETECTION (Real WHO + NCBI + Amina AI)
═══════════════════════════════════════════════════

=== ON-DEMAND (user types /threats) ===

User (Telegram): "/threats"
Orchestrator:     intent=threat → _handle_threat()
                  _ensure_subscribed(user_id, "london") → already done
                  GET http://localhost:8100/threats/london/report
                  Layer 3 returns pre-formatted report:
                  → "🛡 Threat Report: London
                     🔴 Air Quality: Poor (4/5)
                       PM2.5: 65.2 µg/m³
                     🧬 Active Monitoring:
                       🟠 Influenza A(H5N1) - France (moderate) [WHO-EURO]
                       🔴 Measles - United Kingdom (high) [WHO-UK]
                     🔬 Evidence-based — Influenza
                       🎯 Goal: Support immune response
                       • Quercetin: Inhibits neuraminidase
                       • EGCG: Blocks hemagglutinin binding
                     🍽 Top Foods: Green Tea, Turmeric, Red Onions"
                  → Chain to Nutrition Agent with boost_nutrients
Nutrition Agent:  Adapts today's meal plan with biodefense foods
                  → "🍽 I've updated your meal plan:
                     Lunch: Add red onion salad (Quercetin)
                     Afternoon: Green tea instead of coffee (EGCG)"

=== PROACTIVE (webhook from Layer 3 — no user action) ===

[Layer 3 runs hourly WHO DON + NCBI + Amina AI refresh]
Layer 3: Detects London threats changed (new fingerprint)
         _fire_webhooks(["london"])
         POST http://127.0.0.1:8200/threat-alert
         { event: "threat_alert", city: "London",
           report_text: "🛡 Threat Report: London...",
           active_threats: [...], priority_foods: [...] }

Layer 2: _handle_proactive_alert()
         Look up: _city_users["london"] = {"7599032986"}
         Push to Telegram:
         → "🔔 Proactive Threat Alert
            (new threats detected for London)

            🛡 Threat Report: London
            ..."
         Auto-chain to Nutrition Agent:
         → "🍽 Auto-Adapted Meal Plan
            Based on new threats, here's your updated plan..."

═══════════════════════════════════════════════════
BACKGROUND: Layer 3 Pipeline (runs autonomously)
═══════════════════════════════════════════════════

Every 1 hour (server.py → _refresh_loop):
  Step 1: WHO DON OData API → real outbreak data
          3-tier filtering: UK (high) → EURO (moderate) → Global (low)
  Step 2: NCBI Entrez → protein sequences for detected diseases
  Step 3: Amina AI → protein analysis + compound scoring
          Research Agent → FLock LLM for unknown diseases
  Step 4: For each of 25 UK cities:
          AQI + outbreaks → nutrient mapping → report → fingerprint
  Step 5: Compare fingerprints → fire webhooks for changed cities

┌──────────────────────────────────────────────────┐
│           STAYS ON USER'S MACHINE                 │
│           (never leaves)                          │
│                                                   │
│   • User profile (name, age, weight, etc.)       │
│   • Meal plans generated                          │
│   • Daily meal logs                               │
│   • Conversation history                          │
│   • Per-user city→user registry                   │
│                                                   │
│   Stored in: data/profiles/*.json                │
│              data/meals/*.json                    │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│           LEAVES MACHINE (minimal)                │
│                                                   │
│   → FLock API: user messages (for LLM response)  │
│     (same as using ChatGPT — text only)          │
│                                                   │
│   → Threat API: city name + anonymous callback   │
│     POST /subscribe {city: "london",             │
│       callback_url: "http://127.0.0.1:8200/..."}│
│     GET /threats/london/report                   │
│     (no user ID, no name, no health data)        │
│                                                   │
│   → Telegram/Discord/WhatsApp: reply messages    │
│     (standard chat — same as any bot)            │
└──────────────────────────────────────────────────┘




User: "Hey I'm Sarah, 28 years old, trying to lose weight"
    │
    ▼
┌─────────────────────────────────────────────┐
│  FLock API (qwen3-30b)                      │
│                                             │
│  System: "Extract health profile fields     │
│           from user message as JSON"        │
│                                             │
│  Output: {                                  │
│    "name": "Sarah",                         │
│    "age": "28",                             │
│    "goal": "cut",                           │
│    "missing": ["sex","weight","height",     │
│                "allergies","diet","city"],   │
│    "reply": "Hi Sarah! Great goal! I need   │
│     a few more details — what's your        │
│     weight and height?"                     │
│  }                                          │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  Your existing validators                   │
│  validate_name("Sarah") → ✅               │
│  validate_age("28") → ✅                   │
│  validate_goal("cut") → ✅                 │
└──────────────┬──────────────────────────────┘
               │
               ▼
    3 fields saved, 6 remaining
    Bot sends FLock's natural reply to user



Option A (current — MVP):
  Telegram ←→ orchestrator.py (polling + webhook receiver :8200)
                    │                          ▲
                    ├──→ FLock API              │ POST /threat-alert
                    ├──→ local JSON files       │
                    └──→ Layer 3 (:8100) ───────┘


Option B:
  Telegram ←→ OpenClaw Gateway ←→ hooks ←→ orchestrator.py (:8200)
                                                   │          ▲
                                                   ├──→ FLock  │ webhook
                                                   └──→ L3 ───┘


Option C (future):
  Telegram ←→ OpenClaw Gateway ←→ FLock API (as agent model)
                    │
                    ▼
              runs local scripts (calculate_macros.py, etc.)
                    │
                    ▼
              local JSON files


┌─────────────────────────────────────────────────────────────┐
│                        USER (Telegram)                       │
│                                                              │
│   Sends: "Hey I'm Sarah, 28, trying to lose weight"        │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              TELEGRAM BOT API                                │
│              api.telegram.org                                │
│              (receives messages, sends replies)              │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              agents/orchestrator.py                           │
│              (your bot — runs on YOUR machine)               │
│                                                              │
│   + Webhook Receiver on port 8200                            │
│     POST /threat-alert ← Layer 3 webhooks                   │
│                                                              │
│   ┌───────────────────────────────────────────────────────┐ │
│   │  Mode A: FLock Agent (if FLOCK_API_KEY set)           │ │
│   │                                                       │ │
│   │  User message                                         │ │
│   │      │                                                │ │
│   │      ▼                                                │ │
│   │  FLock API (api.flock.io/v1)                         │ │
│   │  Model: qwen3-30b-a3b-instruct-2507                  │ │
│   │      │                                                │ │
│   │      ▼                                                │ │
│   │  Returns JSON:                                        │ │
│   │  { "extracted": {"name":"Sarah","age":"28"},          │ │
│   │    "reply": "Hi Sarah! What's your weight?" }         │ │
│   │      │                                                │ │
│   │      ▼                                                │ │
│   │  Your validators (validate_name, validate_age...)     │ │
│   │      │                                                │ │
│   │      ▼                                                │ │
│   │  Store valid fields → data/profiles/<chat_id>.json    │ │
│   │  Send reply back to Telegram                          │ │
│   │  If all 9 complete → auto-subscribe to Layer 3        │ │
│   └───────────────────────────────────────────────────────┘ │
│                                                              │
│   ┌───────────────────────────────────────────────────────┐ │
│   │  Mode B: Step-by-step (fallback, no API key)          │ │
│   │                                                       │ │
│   │  Rigid Q&A: Question 1 → Answer → Question 2 → ...   │ │
│   │  No LLM needed                                        │ │
│   └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘