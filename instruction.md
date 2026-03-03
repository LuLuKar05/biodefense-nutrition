# Biodefense Nutrition Project Plan

## Overview
A personalized nutrition, bioinformatics, and decentralized AI platform that dynamically adjusts dietary recommendations based on local health threats (like viral outbreaks). The system detects threats, simulates viral structures, screens natural food compounds for neutralization, and pushes pathogen-resistant meal plans to users.

---

## Phase 1: Get User Data & Target Body Goal (The Nutritionist Agent)
**Concept:** By using the user’s health condition (allergies), preferred food types, target body goals, daily calorie burn, and previous nutritional intake, the system calculates exact macronutrient needs and generates a highly personalized daily meal plan.
**Inputs:** Initial data (allergy, preferred diet types, current weight, height, daily calorie burn, location).
**Recommendation & MVP Scope:** 
- For the hackathon MVP, stick to **user input** rather than attempting complex wearable APIs or food image recognition. 
- *Pro Tip:* If time permits, integrate a simple text-to-nutrition API (like Nutritionix Natural Language API) so users can log meals easily by typing (e.g., "I ate an apple and a turkey sandwich").

## Phase 2: Threat Detection & Target Acquisition (The Biodefence Radar)
**Concept:** Actively monitors the user's location against environmental APIs (AQI) and public health databases. If it detects a localized outbreak (e.g., H5N1), it pulls the genetic blueprint of the threat.
**Usage:** Z.ai Agent + NCBI GenBank API + Public Health/AQI APIs.
**Inputs:** User Location.
**Outputs:** Raw Amino Acid Sequence (1D text data) of the circulating virus's target protein (e.g., H5N1 Hemagglutinin spike).

## Phase 3: Structure Prediction (Taking the 3D Mugshot)
**Concept:** Automatically pipes the amino acid sequence into a computational biology engine to simulate its physics and predict its exact 3D shape.
**Usage:** Amina CLI (ESMFold).
**Inputs:** Amino Acid Sequence of the detected virus.
**Outputs:** A `.pdb` file containing the 3D atomic structure of the viral protein spike.
**Recommendation for Phase 3 & 4:** 
- Keep this running at the **system/backend layer** rather than the app layer. 
- Since ESMFold/DiffDock are compute-heavy, offloading to the Amina CLI cloud cluster is the right choice. Use a background worker (e.g., Node.js cron job or Python Celery) to run these based on geographic zones to minimize app latency and redundant compute.

## Phase 4: Phytochemical Library Screening (Molecular Docking)
**Concept:** Acts as a virtual lab. Takes a curated library of natural food compounds (phytochemicals) and simulates throwing them at the 3D virus structure to see which ones bind to and neutralize the virus concurrently.
**Usage:** Amina CLI (DiffDock) + PubChem Database.
**Inputs:** 3D Virus Structure + SMILES strings of known phytochemicals (e.g., Quercetin).
**Outputs:** JSON file containing `[threat_name, top_ligand, confidence_score]`.

### Data Mapping Link (Phase 4 -> 5)
**Recommendation:**
- You will need to map chemical compounds (SMILES) to everyday foods to provide meal advice.
- **FooDB** (foodb.ca) is the best resource for this, but downloading its massive database isn't feasible for a short hackathon. 
- *Hackathon Hack:* Create a **static JSON file** pre-populated with the top 20-30 known antiviral phytochemicals (e.g., Quercetin -> Red Onions, Apples; EGCG -> Green Tea; Allicin -> Garlic) and query this file instantly via your Agent.

## Phase 5: App Layer Integration & Alert (The Defense Protocol)
**Concept:** Agent 3 (Nutritionist) receives molecular docking results, cross-references the winning "ligand" (e.g., Quercetin) with everyday foods, and dynamically rewrites the user's meal plan to feature these foods—while keeping them aligned with Phase 1 fitness goals.
**Usage:** Z.ai + FLock Alliance.
**Inputs:** JSON output from Phase 4 + Phase 1 user baseline data.
**Outputs:** Real-time mobile push alert and dynamically adjusted, pathogen-resistant meal plan.
**Recommendation:** 
- Use **FLock Alliance** for **Federated Learning**. Aggregate user efficacy data locally (e.g., checking if users on the adjusted diet reported fewer symptoms) to adjust the chemical-to-food recommendation weights globally without exposing private health data.