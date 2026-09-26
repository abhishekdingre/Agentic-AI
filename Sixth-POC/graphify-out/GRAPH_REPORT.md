# Graph Report - data  (2026-09-19)

## Corpus Check
- Corpus is ~7,444 words - fits in a single context window. You may not need a graph.

## Summary
- 35 nodes · 76 edges · 5 communities
- Extraction: 95% EXTRACTED · 4% INFERRED · 1% AMBIGUOUS · INFERRED: 3 edges (avg confidence: 0.88)
- Token cost: 99,948 input · 0 output

## Community Hubs (Navigation)
- AXN-2401 Compound, Salt Form & Preclinical Toxicology
- CRB3 Target & Mechanism of Action
- PF-7 Indication & Competitive Landscape
- hERG Cardiac Safety Signal (Contested)
- FBX9 Biomarker & Pharmacodynamics

## God Nodes (most connected - your core abstractions)
1. `AXN-2401 (CRB3 Antagonist Compound)` - 10 edges
2. `PF-7 (CRB3-High Pulmonary Fibrosis Subtype)` - 8 edges
3. `CRB3 and FBX9 Biomarker Panel for Diagnosing PF-7` - 7 edges
4. `CRB3 (Cribriform Repeat-Binding Protein 3)` - 7 edges
5. `FBX9 (CRB3 Pathway Pharmacodynamic Biomarker)` - 7 edges
6. `CRB3 Signaling in Fibrotic Remodeling: A Review` - 6 edges
7. `AXN-2401-102: A Phase 1b Multiple-Ascending-Dose Study with CRB3 Pathway Biomarkers in PF-7 Patients` - 6 edges
8. `AXN-2401-201: A Phase 2a Proof-of-Concept Efficacy Study in PF-7` - 6 edges
9. `PK/PD Modeling Report: Dose Projection for CRB3 Target Engagement` - 6 edges
10. `CRB3 Pathway Dysregulation Defines a Molecular Subtype of Pulmonary Fibrosis (PF-7)` - 5 edges

## Surprising Connections (you probably didn't know these)
- `Internal Pharmacology Report: CRB3 Target Validation Program Summary` --references--> `28-Day Rat Toxicology Study Report for AXN-2401 (Corrected Re-Analysis)`  [AMBIGUOUS]
  corpus/internal_reports/IR-001.md → corpus/internal_reports/IR-002-v2.md
- `The Changing Landscape of Antifibrotic Drug Targets: A Survey` --cites--> `CRB3 Signaling in Fibrotic Remodeling: A Review`  [EXTRACTED]
  corpus/literature/LIT-008.md → corpus/literature/LIT-001.md
- `Competitive Landscape Analysis: CRB3-Targeted Antifibrotics` --semantically_similar_to--> `The Changing Landscape of Antifibrotic Drug Targets: A Survey`  [INFERRED] [semantically similar]
  corpus/internal_reports/IR-005.md → corpus/literature/LIT-008.md
- `Method of Treating CRB3-Driven Pulmonary Fibrosis Subtypes with CRB3 Antagonists` --references--> `PF-7 (CRB3-High Pulmonary Fibrosis Subtype)`  [EXTRACTED]
  corpus/patents/PAT-002.md → corpus/literature/LIT-002.md
- `Combination Therapy of AXN-2401 with Standard-of-Care Antifibrotics` --references--> `AXN-2401 (CRB3 Antagonist Compound)`  [EXTRACTED]
  corpus/patents/PAT-005.md → corpus/literature/LIT-003.md

## Hyperedges (group relationships)
- **hERG Cardiac Safety Signal: External vs Internal Evidence** — corpus_literature_lit_007, corpus_internal_reports_ir_006, corpus_clinical_trials_ct_004, herg_cardiac_safety_signal [EXTRACTED 1.00]
- **PF-7 Biomarker-Based Patient Stratification Pipeline** — corpus_literature_lit_002, corpus_patents_pat_003, corpus_clinical_trials_ct_007 [INFERRED 0.85]
- **AXN-2401 Discovery-to-Clinic Translational Pipeline** — corpus_literature_lit_003, corpus_literature_lit_004, corpus_internal_reports_ir_004, corpus_clinical_trials_ct_001 [INFERRED 0.85]

## Communities (5 total, 0 thin omitted)

### Community 0 - "AXN-2401 Compound, Salt Form & Preclinical Toxicology"
Cohesion: 0.33
Nodes (9): AXN-2401 (CRB3 Antagonist Compound), Form A Crystalline Hydrochloride Salt of AXN-2401, AXN-2401-101: A Phase 1 First-in-Human Safety and Pharmacokinetics Study in Healthy Volunteers, 28-Day Rat Toxicology Study Report for AXN-2401, 28-Day Rat Toxicology Study Report for AXN-2401 (Corrected Re-Analysis), Formulation and CMC Development Report for AXN-2401 Tablets, Pharmacokinetics and Metabolism of AXN-2401 in Preclinical Species, Composition of Matter: AXN-2401 and Related CRB3-Antagonist Compounds (+1 more)

### Community 1 - "CRB3 Target & Mechanism of Action"
Cohesion: 0.36
Nodes (8): CRB3 (Cribriform Repeat-Binding Protein 3), CRB3/FBX9 Signaling Axis (Mechanism), Internal Pharmacology Report: CRB3 Target Validation Program Summary, CRB3 Signaling in Fibrotic Remodeling: A Review, Discovery and Structure-Activity Relationships of AXN-2401, a Selective CRB3 Antagonist, Elevated CRB3 Expression in Lung Tissue from PF-7 Patients: An Immunohistochemistry Survey, Method of Treating CRB3-Driven Pulmonary Fibrosis Subtypes with CRB3 Antagonists, Combination Therapy of AXN-2401 with Standard-of-Care Antifibrotics

### Community 2 - "PF-7 Indication & Competitive Landscape"
Cohesion: 0.48
Nodes (7): PF-7 (CRB3-High Pulmonary Fibrosis Subtype), AXN-2401-301: Phase 3 Study Design and Protocol Summary in PF-7 (Ongoing, Interim), Competitive Landscape Analysis: CRB3-Targeted Antifibrotics, Regulatory Strategy Briefing: AXN-2401 in PF-7, CRB3 Pathway Dysregulation Defines a Molecular Subtype of Pulmonary Fibrosis (PF-7), The Changing Landscape of Antifibrotic Drug Targets: A Survey, CRB3 and FBX9 Biomarker Panel for Diagnosing PF-7

### Community 3 - "hERG Cardiac Safety Signal (Contested)"
Cohesion: 0.53
Nodes (6): AXN-2401-202: A Phase 2 Cardiac Safety Substudy (Holter ECG and QTc Analysis), AXN-2401-203: A Phase 2b Dose-Ranging Study in PF-7, AXN-2401-204: A Phase 2 Open-Label Extension, Long-Term Safety in PF-7, Internal Cardiac Safety Review: hERG and QTc Assessment for AXN-2401, In Vitro hERG Channel Inhibition by AXN-2401: A Cardiac Safety Signal, hERG Cardiac Safety Signal (contested)

### Community 4 - "FBX9 Biomarker & Pharmacodynamics"
Cohesion: 0.90
Nodes (5): FBX9 (CRB3 Pathway Pharmacodynamic Biomarker), AXN-2401-102: A Phase 1b Multiple-Ascending-Dose Study with CRB3 Pathway Biomarkers in PF-7 Patients, AXN-2401-201: A Phase 2a Proof-of-Concept Efficacy Study in PF-7, PK/PD Modeling Report: Dose Projection for CRB3 Target Engagement, AXN-2401 Attenuates Bleomycin-Induced Lung Fibrosis in CRB3-Humanized Mice

## Ambiguous Edges - Review These
- `Internal Pharmacology Report: CRB3 Target Validation Program Summary` → `28-Day Rat Toxicology Study Report for AXN-2401 (Corrected Re-Analysis)`  [AMBIGUOUS]
  corpus/internal_reports/IR-001.md · relation: references

## Knowledge Gaps
- **1 isolated node(s):** `28-Day Rat Toxicology Study Report for AXN-2401`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Internal Pharmacology Report: CRB3 Target Validation Program Summary` and `28-Day Rat Toxicology Study Report for AXN-2401 (Corrected Re-Analysis)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `AXN-2401 (CRB3 Antagonist Compound)` connect `AXN-2401 Compound, Salt Form & Preclinical Toxicology` to `CRB3 Target & Mechanism of Action`, `hERG Cardiac Safety Signal (Contested)`?**
  _High betweenness centrality (0.315) - this node is a cross-community bridge._
- **Why does `In Vitro hERG Channel Inhibition by AXN-2401: A Cardiac Safety Signal` connect `hERG Cardiac Safety Signal (Contested)` to `AXN-2401 Compound, Salt Form & Preclinical Toxicology`, `PF-7 Indication & Competitive Landscape`?**
  _High betweenness centrality (0.135) - this node is a cross-community bridge._
- **Why does `CRB3 (Cribriform Repeat-Binding Protein 3)` connect `CRB3 Target & Mechanism of Action` to `AXN-2401 Compound, Salt Form & Preclinical Toxicology`, `PF-7 Indication & Competitive Landscape`?**
  _High betweenness centrality (0.117) - this node is a cross-community bridge._
- **What connects `28-Day Rat Toxicology Study Report for AXN-2401` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._