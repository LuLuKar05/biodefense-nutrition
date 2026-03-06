# Biodefense Nutrition Project Plan

## Overview
A personalized nutrition, bioinformatics, and decentralized AI platform that dynamically adjusts dietary recommendations based on local health threats (like viral outbreaks). The system detects threats, simulates viral structures, screens natural food compounds for neutralization, and pushes pathogen-resistant meal plans to users.

### Three-Layer Architecture
1. **OpenClaw Gateway** — Multi-channel chat gateway (Telegram, Discord, WhatsApp, Slack, WebChat, 20+ more). Runs locally. Receives/delivers messages.
2. **Agent Orchestrator** — Python agent coordinator. Routes user intent to specialized sub-agents. Each agent uses **FLock API** (`api.flock.io/v1`) as the LLM brain for natural conversation.
3. **Threat Intelligence Backend** — Zero-knowledge FastAPI service. Public data only (AQI, outbreaks, docking results). Never receives user PII.

### Products & Tools
| Product | Purpose | Where It Runs |
|---------|---------|---------------|
| **OpenClaw** | Multi-channel gateway (22+ platforms) | Locally (Node.js) |
| **FLock API** | LLM brain — powers all agents via OpenAI-compatible API | FLock servers (`api.flock.io`) |
| **FLock Training** | Federated learning — local train, weight-only export | Locally (Python) |
| **Agent Orchestrator** | Routes intent to sub-agents | Locally (Python) |
| **FastAPI** | Threat intelligence REST API (zero-knowledge) | Docker / cloud |
| **Celery + Redis** | Background tasks (folding, docking, scanning) | Docker |
| **Next.js** | System dashboard for hackathon judges | Docker |
| **MongoDB** | Public threat data only (zero user data) | Docker |

---

## Phase 1: Get User Data & Target Body Goal (The Nutritionist Agent)
**Concept:** Collect user health profile through natural AI-powered conversation. Calculate macros and generate personalized meal plans. All data stays local.

**How it works:**
- OpenClaw receives message from any channel → forwards to Agent Orchestrator
- Orchestrator checks if profile exists (`data/profiles/<chat_id>.json`)
- If not → routes to **Onboarding Agent** (natural conversation via FLock API, validates with Python validators)
- If yes → detects intent → routes to **Nutrition Agent** (TDEE calc, meal plans via FLock API)
- User can give multiple fields at once: "I'm Sarah, 28, trying to lose weight" → agent extracts all 3
- All extracted data passes through validators before being saved

**Sub-Agents involved:** Onboarding Agent, Nutrition Agent
**Key files:** `agents/onboarding_agent.py`, `agents/nutrition_agent.py`, `agents/tools/macro_calculator.py`, `agents/tools/validators.py`

**Inputs:** Name, age, sex, weight (kg), height (cm), allergies, diet type, body goal, city.
**Recommendation & MVP Scope:**
- For the hackathon MVP, stick to **user input via chat** rather than attempting complex wearable APIs or food image recognition.
- FLock API handles natural language — no rigid Q&A flow needed.
- Fallback: If FLock API unavailable, agents degrade to step-by-step mode.

## Phase 2: Threat Detection & Target Acquisition (The Biodefence Radar)
**Concept:** Actively monitors the user's location against environmental APIs (AQI) and public health databases. If it detects a localized outbreak (e.g., H5N1), it pulls the genetic blueprint of the threat.

**How it works:**
- **Threat Agent** queries `GET /threats?zone=<city>` (anonymous, no user identity)
- Backend runs Celery workers that scan AQI + public health feeds every 6 hours
- If threat detected, Orchestrator chains to Meal Adaptation Agent

**Sub-Agents involved:** Threat Agent → chains to Meal Adaptation Agent
**Key files:** `agents/threat_agent.py`, `agents/tools/threat_api_client.py`

**Usage:** Agent Orchestrator + NCBI GenBank API + Public Health/AQI APIs.
**Inputs:** User Location (city-level, from local profile).
**Outputs:** Raw Amino Acid Sequence (1D text data) of the circulating virus's target protein (e.g., H5N1 Hemagglutinin spike).

## Phase 3: Structure Prediction (Taking the 3D Mugshot)
**Concept:** Automatically pipes the amino acid sequence into a computational biology engine to simulate its physics and predict its exact 3D shape.
**Usage:** Amina CLI (ESMFold).
**Inputs:** Amino Acid Sequence of the detected virus.
**Outputs:** A `.pdb` file containing the 3D atomic structure of the viral protein spike.
**Recommendation for Phase 3 & 4:**
- Keep this running at the **system/backend layer** rather than the app layer.
- Since ESMFold/DiffDock are compute-heavy, offloading to the Amina CLI cloud cluster is the right choice. Use Celery background workers to run these based on geographic zones.

## Phase 4: Phytochemical Library Screening (Molecular Docking)
**Concept:** Acts as a virtual lab. Takes a curated library of natural food compounds (phytochemicals) and simulates throwing them at the 3D virus structure to see which ones bind to and neutralize the virus concurrently.
**Usage:** Amina CLI (DiffDock) + PubChem Database.
**Inputs:** 3D Virus Structure + SMILES strings of known phytochemicals (e.g., Quercetin).
**Outputs:** JSON file containing `[threat_name, top_ligand, confidence_score]`.

### Data Mapping Link (Phase 4 -> 5)
**Recommendation:**
- Map chemical compounds (SMILES) to everyday foods using `agents/tools/phytochem_lookup.py` + `data/phytochemicals.json`.
- Pre-populated with top 20-30 known antiviral phytochemicals (Quercetin → Red Onions, Apples; EGCG → Green Tea; Allicin → Garlic).

## Phase 5: App Layer Integration & Alert (The Defense Protocol)
**Concept:** Meal Adaptation Agent receives molecular docking results from Threat Agent, cross-references the winning "ligand" with everyday foods, and dynamically rewrites the user's meal plan to feature biodefense foods — while keeping them aligned with Phase 1 fitness goals.

**How it works:**
- Orchestrator chains: Threat Agent → Meal Adaptation Agent
- Meal Adaptation Agent uses FLock API + `agents/tools/phytochem_lookup.py` + local profile
- Generates adapted meal plan featuring biodefense foods
- Delivers alert + adapted plan to user on ALL connected channels

**Sub-Agents involved:** Threat Agent → Meal Adaptation Agent → FLock Training Agent
**Key files:** `agents/meal_adaptation_agent.py`, `agents/flock_agent.py`, `agents/tools/phytochem_lookup.py`

**Usage:** Agent Orchestrator + FLock API + FLock Alliance.
**Inputs:** JSON output from Phase 4 + Phase 1 user baseline data (from local profile).
**Outputs:** Real-time alert on all channels and dynamically adjusted, pathogen-resistant meal plan.
**Recommendation:**
- Use **FLock Alliance** for **Federated Learning**. FLock Training Agent aggregates user efficacy data locally, trains local model, exports only weights — never raw health data.
- Use **FLock API** for the LLM that composes the adapted meal plan naturally.
