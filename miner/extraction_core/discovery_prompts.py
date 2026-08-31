# -*- coding: utf-8 -*-
"""材料识别（Material Discovery）— Prompt 模板
"""

PROMPT_ELECTROLYTE_DISCOVERY = """You are an electrolyte formulation analyst. Scan the paper below and identify ALL distinct electrolyte formulations and compositions relevant to {component_type} research.

For each distinct electrolyte formulation found, output a JSON object with:
- name: Full descriptive name of the electrolyte formulation (e.g., "1M LiPF6 in EC/DEC (1:1 v/v)", "E-PFPN (1.5M LiTFSI + 0.5M LiDFOB in DME:FEC:PFPN)")
- short_name: Common abbreviation used in the paper (e.g., "LiPF6-EC/DEC", "E-PFPN", "Baseline", "E-DFOB/FEC")
- formula: Simplified composition notation (e.g., "1M LiPF6 EC/DEC", "1.5M LiTFSI+0.5M LiDFOB DME:FEC:PFPN")
- role: One of:
    - "novel" — the primary electrolyte formulation being studied / newly designed
    - "baseline" — a standard/reference electrolyte (e.g., "1M LiPF6 in EC/DEC")
    - "comparison" — another formulation tested alongside for comparison
    - identical formulations with different concentrations / additive amounts, list each separately
- description: One-sentence note capturing key differences (e.g., "with 2%% FEC additive", "HCE formulation", "non-flammable")

STRICT RULES:
1. If the paper studies the same base electrolyte under multiple distinct compositions, additive amounts, or concentrations, list EACH variant as a separate entry.
2. NEVER list electrode materials (NMC, LFP, graphite, Si, Li metal, etc.) as electrolyte materials.
3. NEVER list current collectors (Cu, Al), conductive additives (carbon black, CNT), or binders (PVDF).
4. For baseline/commercial electrolytes, still list them as "baseline" role.
5. If NO {component_type} formulation is found in the paper, return an empty array [].

Paper title and text below:

{text}

Output a JSON array only:
[{{"name": "...", "short_name": "...", "formula": "...", "role": "...", "description": "..."}}, ...]
"""


PROMPT_MATERIAL_DISCOVERY = """You are a battery materials analyst. Scan the paper below and identify ALL electrode materials relevant to {component_type} research that were actually synthesized or prepared by the authors of this paper (excluding any commercial materials, and excluding materials only mentioned from other literature).

For each distinct material found, output a JSON object with:
- name: Full material name or chemical formula (e.g., "LiNi0.8Co0.1Mn0.1O2", "Graphite")
- short_name: Common abbreviation used in the paper (e.g., "NCM811", "Si/C composite")
- formula: Simplified chemical formula or composition notation
- role: One of:
    - "novel" — the primary material being studied / newly synthesized by the authors
    - "comparison" — a material synthesized by the authors as a control or alternative (e.g., different synthesis method, different composition) — do NOT use this for commercial materials or materials from other literature
    - "commercial" — a commercial/reference material (e.g., commercial NCM622, commercial graphite)
    - identical materials with different doping / processing conditions, list each separately
- description: One-sentence note capturing key differences (e.g., "5% Al-doped", "different electrolyte", "cycled at 60 C")

REMEMBER: you must ONLY return active electrode materials that the authors made and tested in this work.

The following items are NOT electrode materials — do NOT list them as separate entries:
- Cu foil, Al foil, Cu mesh, Ni foam → current collectors (ignore them)
- Carbon black, Super P, CNT, VGCF when mentioned as conductive additive → conductive additives (ignore them)
- PVDF, PTFE, CMC, SBR, PEO → binders (ignore them)
- Li metal, Na metal when used as counter/reference electrode → NOT the studied material (unless the paper explicitly studies Li/Na metal itself as the anode)
- Separators, glass fiber → cell components
- Stainless steel, coin cell casing → hardware
- Electrolyte, LiPF6, EC, DEC → electrolyte components
- Commercial materials (e.g., commercial graphite, commercial NCM, commercial LiCoO2) even if tested → do NOT output them
- Materials only mentioned from other literature (e.g., "compared with material X reported in previous work") without being synthesized in this paper → do NOT output them
However, if CNT, rGO, graphene, or carbon is used as the structural scaffold/backbone of the active material (e.g., "Ag14@NC" where NC is nitrogen-doped rGO/CNT scaffold, or "Si/C composite" where C is the carbon matrix), include it as part of the material name — the scaffold IS part of the active material.

STRICT RULES:
1. If the paper discusses the same base material under multiple distinct processing or composition variants (different doping levels, coating amounts, etc.), list EACH variant as a separate entry, but only if the authors actually prepared each variant.
2. If NO {component_type} material is found, return an empty array [].
3. When in doubt about whether to include something, exclude it.
4. Always use the full material designation from the paper (e.g., "Ag1.3@C" not "Ag1.3").

Paper title and text below:

{text}

Output a JSON array only:
[{{"name": "...", "short_name": "...", "formula": "...", "role": "...", "description": "..."}}, ...]
"""
