# -*- coding: utf-8 -*-
"""
电解质提取示例
每个标签包含示例段落及对应的 JSON 输出，遵循 information 和 structured_data 定义。
"""

# ==================== Agent 1: 材料本征属性 ====================

Li_Solvent_Binding_Energy = """
Paragraph: DFT calculations at the PBE0/def2-TZVP level with implicit solvation (PCM) gave a Li⁺-EC binding energy of -2.68 eV and Li⁺-DMC of -2.15 eV, indicating stronger solvation by EC due to its higher donicity.
JSON: ```JSON
[{"value": -2.68, "unit": "eV", "solvent": "EC", "method": "DFT PBE0/def2-TZVP with PCM", "source_text": "DFT calculations gave a Li\u207a-EC binding energy of -2.68 eV"},
{"value": -2.15, "unit": "eV", "solvent": "DMC", "method": "DFT PBE0/def2-TZVP with PCM", "source_text": "and Li\u207a-DMC of -2.15 eV"}]
```
"""
Li_Anion_Binding_Energy = """
Paragraph: The Li⁺-FSI⁻ binding energy was calculated to be -3.45 eV in the gas phase at the M06-2X level, which decreased to -2.78 eV when solvation was considered via the SMD model.
JSON: ```JSON
[{"value": -3.45, "unit": "eV", "anion": "FSI\u207b", "method": "DFT M06-2X gas phase", "source_text": "The Li\u207a-FSI\u207b binding energy was calculated to be -3.45 eV in the gas phase at the M06-2X level"},
{"value": -2.78, "unit": "eV", "anion": "FSI\u207b", "method": "DFT M06-2X SMD", "source_text": "decreased to -2.78 eV when solvation was considered via the SMD model"}]
```
"""

TM_Solvent_Binding_Energy = """
Paragraph: DFT calculations showed that the Mn²⁺-EC binding energy is -4.52 eV, significantly stronger than Mn²⁺-DMC (-3.87 eV), suggesting EC is more effective at sequestering dissolved Mn²⁺ from the cathode.
JSON: ```JSON
[{"value": -4.52, "unit": "eV", "metal_ion": "Mn\u00b2\u207a", "solvent": "EC", "method": "DFT", "source_text": "DFT calculations showed that the Mn\u00b2\u207a-EC binding energy is -4.52 eV"},
{"value": -3.87, "unit": "eV", "metal_ion": "Mn\u00b2\u207a", "solvent": "DMC", "method": "DFT", "source_text": "significantly stronger than Mn\u00b2\u207a-DMC (-3.87 eV)"}]
```
"""

Li_Anion_Coordination_Number_MD = """
Paragraph: MD simulations of 1 M LiFSI in DME at 300 K revealed an average Li⁺-anion coordination number of 1.2, indicating a substantial fraction of contact ion pairs and aggregates in the solvation structure.
JSON: ```JSON
[{"value": 1.2, "unit": "", "simulation_conditions": "1 M LiFSI in DME, 300 K", "source_text": "MD simulations of 1 M LiFSI in DME at 300 K revealed an average Li\u207a-anion coordination number of 1.2"}]
```
"""

Li_Solvent_Coordination_Number_MD = """
Paragraph: From the same MD trajectory, the average Li⁺-solvent coordination number was 3.8, confirming that DME molecules dominate the first solvation shell in the 1 M LiFSI/DME electrolyte.
JSON: ```JSON
[{"value": 3.8, "unit": "", "simulation_conditions": "1 M LiFSI in DME, 300 K", "source_text": "the average Li\u207a-solvent coordination number was 3.8"}]
```
"""

Molecule_Formation_Energy = """
Paragraph: The formation energy of fluoroethylene carbonate (FEC) was calculated to be -1.92 eV/atom relative to its constituent elements, indicating moderate thermodynamic stability compared to EC (-2.04 eV/atom).
JSON: ```JSON
[{"value": -1.92, "unit": "eV/atom", "molecule": "FEC", "method": "DFT", "source_text": "The formation energy of FEC was calculated to be -1.92 eV/atom relative to its constituent elements"}]
```
"""

CIP_AGG_Fraction = """
Paragraph: Raman spectroscopy of 1.5 M LiTFSI in DOL/DME (1:1) showed that the contact ion pair (CIP) fraction was 42%, aggregate (AGG) fraction 28%, and free ions 30%, indicating extensive ion pairing at this concentration.
JSON: ```JSON
[{"value": {"CIP": 42, "AGG": 28}, "unit": "%", "conditions": "1.5 M LiTFSI in DOL/DME (1:1), 25 \u00b0C", "source_text": "Raman spectroscopy of 1.5 M LiTFSI in DOL/DME (1:1) showed that the CIP fraction was 42%, AGG fraction 28%"}]
```
"""
Solvent_van_der_Waals_Volume = """
Paragraph: The van der Waals volume of EC was computed to be 62.3 cm³/mol, while that of DMC is 79.1 cm³/mol, reflecting the more compact structure of the cyclic carbonate.
JSON: ```JSON
[{"value": 62.3, "unit": "cm\u00b3/mol", "solvent": "EC", "method": "DFT", "source_text": "The van der Waals volume of EC was computed to be 62.3 cm\u00b3/mol"},
{"value": 79.1, "unit": "cm\u00b3/mol", "solvent": "DMC", "method": "DFT", "source_text": "while that of DMC is 79.1 cm\u00b3/mol"}]
```
"""

Mixing_Entropy = """
Paragraph: The mixing entropy of the ternary solvent system EC/EMC/DEC = 2:5:3 (vol%) was calculated to be ΔS_mix = 8.7 J mol⁻¹ K⁻¹ based on the ideal mixing formula.
JSON: ```JSON
[{"value": 8.7, "unit": "J mol\u207b\u00b9 K\u207b\u00b9", "derived": true, "source_text": "The mixing entropy of the ternary solvent system EC/EMC/DEC = 2:5:3 (vol%) was calculated to be \u0394S_mix = 8.7 J mol\u207b\u00b9 K\u207b\u00b9"}]
```
"""
Dipole_Moment = """
Paragraph: The dipole moment of propylene carbonate (PC) is 4.94 D, higher than that of EC (4.61 D) and DMC (3.87 D), accounting for its higher dielectric constant.
JSON: ```JSON
[{"value": 4.94, "unit": "D", "molecule": "PC", "method": "experimental", "source_text": "The dipole moment of PC is 4.94 D"},
{"value": 4.61, "unit": "D", "molecule": "EC", "method": "experimental", "source_text": "higher than that of EC (4.61 D)"},
{"value": 3.87, "unit": "D", "molecule": "DMC", "method": "experimental", "source_text": "and DMC (3.87 D)"}]
```
"""

Dielectric_Constant = """
Paragraph: The static dielectric constant of EC at 25 °C is 89.6, while that of PC is 64.9 and DMC is 3.1, explaining the excellent salt dissociation capacity of cyclic carbonates.
JSON: ```JSON
[{"value": 89.6, "unit": "", "temperature_C": 25, "source_text": "The static dielectric constant of EC at 25 \u00b0C is 89.6"},
{"value": 64.9, "unit": "", "temperature_C": 25, "source_text": "while that of PC is 64.9"},
{"value": 3.1, "unit": "", "temperature_C": 25, "source_text": "and DMC is 3.1"}]
```
"""

Fluorination_Degree = """
Paragraph: The fluorination degree of FEC is 3 fluorine atoms per molecule (F/C ratio = 1.5), whereas that of HFE is 6 fluorine atoms (F/C ratio = 3.0).
JSON: ```JSON
[{"value": 3, "unit": "F atoms", "molecule": "FEC", "source_text": "The fluorination degree of FEC is 3 fluorine atoms per molecule"},
{"value": 6, "unit": "F atoms", "molecule": "HFE", "source_text": "whereas that of HFE is 6 fluorine atoms"}]
```
"""

Number_of_Fluorine_Substituents = """
Paragraph: The solvent 1,1,2,2-tetrafluoroethyl-2,2,3,3-tetrafluoropropyl ether (TTE) contains 8 fluorine substituents, which effectively lowers its HOMO energy.
JSON: ```JSON
[{"value": 8, "unit": "", "molecule": "TTE", "source_text": "The solvent TTE contains 8 fluorine substituents"}]
```
"""

HOMO_LUMO_Energy = """
Paragraph: DFT-B3LYP/6-311+G(d,p) calculations gave HOMO/LUMO energies of -8.12 eV / 0.28 eV for FEC and -7.35 eV / 0.52 eV for EC, confirming the higher oxidative stability of fluorinated carbonates.
JSON: ```JSON
[{"value": {"HOMO": -8.12, "LUMO": 0.28, "gap": 8.40}, "unit": "eV", "molecule": "FEC", "method": "DFT-B3LYP/6-311+G(d,p)", "source_text": "DFT calculations gave HOMO/LUMO energies of -8.12 eV / 0.28 eV for FEC"},
{"value": {"HOMO": -7.35, "LUMO": 0.52, "gap": 7.87}, "unit": "eV", "molecule": "EC", "method": "DFT-B3LYP/6-311+G(d,p)", "source_text": "and -7.35 eV / 0.52 eV for EC"}]
```
"""

Melting_Point = """
Paragraph: The melting point of EC is 36.4 °C, which limits its use in low-temperature electrolytes; blending with DMC (mp -4.6 °C) effectively lowers the freezing point.
JSON: ```JSON
[{"value": 36.4, "unit": "\u00b0C", "source_text": "The melting point of EC is 36.4 \u00b0C"},
{"value": -4.6, "unit": "\u00b0C", "source_text": "blending with DMC (mp -4.6 \u00b0C)"}]
```
"""
Boiling_Point = """
Paragraph: EC has a high boiling point of 248 °C, while DMC boils at 90 °C, making EC safer for high-temperature operation but DMC more volatile.
JSON: ```JSON
[{"value": 248, "unit": "\u00b0C", "source_text": "EC has a high boiling point of 248 \u00b0C"},
{"value": 90, "unit": "\u00b0C", "source_text": "while DMC boils at 90 \u00b0C"}]
```
"""

Flash_Point = """
Paragraph: The flash point of EC is 150 °C (closed cup), significantly higher than that of DMC (18 °C), indicating superior fire safety for EC-based electrolytes.
JSON: ```JSON
[{"value": 150, "unit": "\u00b0C", "source_text": "The flash point of EC is 150 \u00b0C (closed cup)"},
{"value": 18, "unit": "\u00b0C", "source_text": "significantly higher than that of DMC (18 \u00b0C)"}]
```
"""
Viscosity = """
Paragraph: The dynamic viscosity of 1 M LiPF₆ in EC/EMC (3:7) was measured to be 4.8 mPa·s at 25 °C, increasing to 8.5 mPa·s at 0 °C.
JSON: ```JSON
[{"value": 4.8, "unit": "mPa\u00b7s", "temperature_C": 25, "source_text": "The dynamic viscosity of 1 M LiPF\u2086 in EC/EMC (3:7) was measured to be 4.8 mPa\u00b7s at 25 \u00b0C"},
{"value": 8.5, "unit": "mPa\u00b7s", "temperature_C": 0, "source_text": "increasing to 8.5 mPa\u00b7s at 0 \u00b0C"}]
```
"""

Density = """
Paragraph: The density of 1 M LiPF₆ in EC/DMC (1:1) is 1.28 g/cm³ at 25 °C, slightly higher than that of the neat solvent mixture (1.19 g/cm³).
JSON: ```JSON
[{"value": 1.28, "unit": "g/cm\u00b3", "temperature_C": 25, "source_text": "The density of 1 M LiPF\u2086 in EC/DMC (1:1) is 1.28 g/cm\u00b3 at 25 \u00b0C"}]
```
"""
#    ==================== Agent 2: 电化学性能 ====================
Li_Desolvation_Activation_Energy = """
Paragraph: Temperature-dependent EIS on a Li||Cu cell from 5 to 35 °C gave a desolvation activation energy of 0.52 eV for 1 M LiPF₆ in EC/DMC (1:1), which decreased to 0.38 eV when 10% FEC was added.
JSON: ```JSON
[{"value": 0.52, "unit": "eV", "method": "EIS Arrhenius", "electrode": "Cu", "source_text": "Temperature-dependent EIS on a Li||Cu cell gave a desolvation activation energy of 0.52 eV for 1 M LiPF\u2086 in EC/DMC (1:1)"},
{"value": 0.38, "unit": "eV", "method": "EIS Arrhenius", "electrode": "Cu", "source_text": "decreased to 0.38 eV when 10% FEC was added"}]
```
"""

Charge_Transfer_Resistance = """
Paragraph: At 25 °C and 50% SOC, the charge transfer resistance of a Li||NMC811 cell with 1 M LiPF₆ in EC/EMC (3:7) was 28.5 Ω, as fitted from the Nyquist plot using a modified Randles circuit.
JSON: ```JSON
[{"value": 28.5, "unit": "\u03a9", "condition_id": "C001", "cycle_number": 0, "temperature_C": 25, "source_text": "the charge transfer resistance was 28.5 \u03a9"}]
```
"""

SEI_Resistance = """
Paragraph: After formation cycling, the SEI resistance on the graphite anode was determined by DRT to be 12.3 Ω at 25 °C, accounting for 35% of the total interfacial resistance.
JSON: ```JSON
[{"value": 12.3, "unit": "\u03a9", "condition_id": "C001", "cycle_number": 5, "source_text": "the SEI resistance on the graphite anode was 12.3 \u03a9 at 25 \u00b0C"}]
```
"""

CEI_Resistance = """
Paragraph: The CEI resistance of the NCM811 cathode after 100 cycles at 1C was 18.7 Ω, measured by EIS and separated via DRT analysis, indicating significant interphase growth.
JSON: ```JSON
[{"value": 18.7, "unit": "\u03a9", "condition_id": "C001", "cycle_number": 100, "source_text": "The CEI resistance of the NCM811 cathode after 100 cycles at 1C was 18.7 \u03a9"}]
```
"""

Li_Transport_Activation_Energy_SEI = """
Paragraph: From the temperature-dependent R_SEI (0 to 40 °C), the activation energy for Li⁺ transport through the SEI was calculated to be 0.28 eV, indicating a relatively low barrier for ion conduction.
JSON: ```JSON
[{"value": 0.28, "unit": "eV", "temperature_range": "0-40 \u00b0C", "source_text": "the activation energy for Li\u207a transport through the SEI was calculated to be 0.28 eV"}]
```
"""

Li_Transport_Activation_Energy_CEI = """
Paragraph: The CEI transport activation energy derived from Arrhenius fitting of R_CEI was 0.31 eV for the baseline electrolyte, which dropped to 0.22 eV with the addition of LiDFOB.
JSON: ```JSON
[{"value": 0.31, "unit": "eV", "temperature_range": "10-50 \u00b0C", "source_text": "The CEI transport activation energy for the baseline electrolyte was 0.31 eV"},
{"value": 0.22, "unit": "eV", "temperature_range": "10-50 \u00b0C", "source_text": "which dropped to 0.22 eV with the addition of LiDFOB"}]
```
"""

SEI_Thickness = """
Paragraph: Cryo-TEM cross-sections of the cycled graphite anode revealed an SEI thickness of 12.5 nm after 50 cycles, consisting of an inner inorganic layer (~4 nm) and an outer organic layer.
JSON: ```JSON
[{"value": 12.5, "unit": "nm", "cycle_number": 50, "method": "cryo-TEM", "source_text": "Cryo-TEM revealed an SEI thickness of 12.5 nm after 50 cycles"}]
```
"""

CEI_Thickness = """
Paragraph: HRTEM imaging of the NCM811 cathode after 100 cycles showed a uniform CEI layer with an average thickness of 8.3 nm, free of cracks.
JSON: ```JSON
[{"value": 8.3, "unit": "nm", "cycle_number": 100, "method": "HRTEM", "source_text": "HRTEM imaging showed a uniform CEI layer with an average thickness of 8.3 nm"}]
```
"""

LiF_Content_in_SEI_CEI = """
Paragraph: XPS depth profiling of the SEI on graphite after 50 cycles indicated a LiF content of 28 at% near the surface and 42 at% at a depth of 15 nm, confirming a LiF-rich inner layer.
JSON: ```JSON
[{"value": 28, "unit": "at%", "interphase": "SEI", "cycle_number": 50, "source_text": "XPS depth profiling indicated a LiF content of 28 at% near the surface"},
{"value": 42, "unit": "at%", "interphase": "SEI", "cycle_number": 50, "source_text": "and 42 at% at a depth of 15 nm"}]
```
"""

Inorganic_Organic_Ratio_SEI_CEI = """
Paragraph: The inorganic/organic ratio in the SEI, derived from XPS C 1s and Li 1s/O 1s spectra, was 1.8 after 20 cycles and increased to 2.5 after 100 cycles, indicating progressive enrichment of inorganic species.
JSON: ```JSON
[{"value": 1.8, "unit": "", "interphase": "SEI", "cycle_number": 20, "source_text": "The inorganic/organic ratio in the SEI was 1.8 after 20 cycles"},
{"value": 2.5, "unit": "", "interphase": "SEI", "cycle_number": 100, "source_text": "and increased to 2.5 after 100 cycles"}]
```
"""

Li2O_Content_in_SEI = """
Paragraph: The Li₂O content in the SEI on the lithium metal anode was quantified by XPS O 1s fitting to be 9.8 at% after 50 cycles, comparable to the LiF content in the same region.
JSON: ```JSON
[{"value": 9.8, "unit": "at%", "cycle_number": 50, "source_text": "The Li\u2082O content in the SEI was 9.8 at% after 50 cycles"}]
```
"""

S_N_Content_in_SEI = """
Paragraph: XPS analysis of the SEI formed from 1 M LiFSI in DME revealed sulfur and nitrogen contents of 5.2 at% and 3.8 at%, respectively, confirming anion-derived decomposition products.
JSON: ```JSON
[{"value": {"S": 5.2, "N": 3.8}, "unit": "at%", "cycle_number": 20, "source_text": "XPS analysis of the SEI revealed sulfur and nitrogen contents of 5.2 at% and 3.8 at%"}]
```
"""
Transition_Metal_Deposition = """
Paragraph: ToF-SIMS depth profiling showed that Mn deposition on the graphite SEI reached 1.2 at% after 200 cycles, with Ni and Co each around 0.3 at%, indicating severe cathode cross-talk.
JSON: ```JSON
[{"value": {"Mn": 1.2, "Ni": 0.3, "Co": 0.3}, "unit": "at%", "interphase": "SEI", "cycle_number": 200, "method": "ToF-SIMS", "source_text": "ToF-SIMS depth profiling showed Mn deposition on the graphite SEI reached 1.2 at% after 200 cycles, with Ni and Co each around 0.3 at%"}]
```
"""

Interfacial_Crack_Density = """
Paragraph: SEM image analysis of cross-sectioned NCM811 secondary particles after 300 cycles at 1C gave a crack density of 0.45 µm⁻¹, with most cracks propagating along grain boundaries.
JSON: ```JSON
[{"value": 0.45, "unit": "\u00b5m\u207b\u00b9", "cycle_number": 300, "method": "SEM", "source_text": "SEM image analysis gave a crack density of 0.45 \u00b5m\u207b\u00b9 after 300 cycles"}]
```
"""
Interface_Roughness = """
Paragraph: AFM imaging of the graphite electrode after SEI formation revealed a root-mean-square roughness of 18.2 nm over a 10×10 µm² area, significantly higher than the pristine surface (2.3 nm).
JSON: ```JSON
[{"value": 18.2, "unit": "nm", "condition": "after SEI formation", "source_text": "AFM imaging revealed a roughness of 18.2 nm after SEI formation"}]
```
"""

Contact_Angle = """
Paragraph: The contact angle of the electrolyte (1 M LiPF₆ in EC/DMC) on a Celgard 2500 separator was 42°, while on a PE membrane it was 58°, indicating better wettability of the Celgard separator.
JSON: ```JSON
[{"value": 42, "unit": "\u00b0", "substrate": "Celgard 2500", "source_text": "The contact angle on a Celgard 2500 separator was 42\u00b0"},
```
"""
Ionic_Conductivity = """
Paragraph: The ionic conductivity of 1 M LiPF₆ in EC/EMC (3:7) was 9.8 mS/cm at 25 °C and decreased to 2.3 mS/cm at -20 °C, as measured by EIS using a stainless steel blocking cell.
JSON: ```JSON
[{"value": 9.8, "unit": "mS/cm", "temperature_C": 25, "method": "EIS blocking cell", "source_text": "The ionic conductivity was 9.8 mS/cm at 25 \u00b0C"},
{"value": 2.3, "unit": "mS/cm", "temperature_C": -20, "method": "EIS blocking cell", "source_text": "decreased to 2.3 mS/cm at -20 \u00b0C"}]
```
"""

Electrochemical_Stability_Window = """
Paragraph: LSV on a Pt working electrode (scan rate 1 mV/s) showed that the electrolyte (1 M LiTFSI in DOL/DME) is stable up to 4.2 V vs. Li/Li⁺, with anodic decomposition starting at 4.1 V (0.05 mA/cm² criterion).
JSON: ```JSON
[{"value": {"min_V": 0.0, "max_V": 4.2}, "unit": "V vs. Li/Li\u207a", "working_electrode": "Pt", "source_text": "LSV on a Pt working electrode showed that the electrolyte is stable up to 4.2 V vs. Li/Li\u207a"}]
```
"""

Anodic_Stability_Onset_Potential = """
Paragraph: The anodic stability onset potential of the 1 M LiPF₆ in EC/EMC (3:7) electrolyte was determined to be 4.5 V vs. Li/Li⁺ using a glassy carbon electrode at a current density of 0.1 mA/cm².
JSON: ```JSON
[{"value": 4.5, "unit": "V vs. Li/Li\u207a", "current_density_criterion": "0.1 mA/cm\u00b2", "source_text": "The anodic stability onset potential was 4.5 V vs. Li/Li\u207a"}]
```
"""

Reduction_Onset_Potential = """
Paragraph: The reduction onset potential of the same electrolyte on a Cu electrode was 0.8 V vs. Li/Li⁺, indicating reductive decomposition at relatively high potential.
JSON: ```JSON
[{"value": 0.8, "unit": "V vs. Li/Li\u207a", "current_density_criterion": "0.05 mA/cm\u00b2", "source_text": "The reduction onset potential on a Cu electrode was 0.8 V vs. Li/Li\u207a"}]
```
"""

Operating_Temperature_Range = """
Paragraph: The Li||NMC811 cell with the optimized electrolyte could be cycled between -20 °C and 60 °C with >80% capacity retention after 200 cycles, defining the practical operating temperature range.
JSON: ```JSON
[{"value": {"min": -20, "max": 60}, "unit": "\u00b0C", "criterion": ">80% capacity retention after 200 cycles", "source_text": "The cell could be cycled between -20 \u00b0C and 60 \u00b0C with >80% capacity retention after 200 cycles"}]
```
"""

Capacity_Retention = """
Paragraph: After 500 cycles at 1C and 25 °C, the NCM811||graphite cell retained 85.3% of its initial discharge capacity, demonstrating excellent long-term stability.
JSON: ```JSON
[{"value": 85.3, "unit": "%", "cycle_number": 500, "condition_id": "C001", "source_text": "After 500 cycles at 1C and 25 \u00b0C, the cell retained 85.3% of its initial capacity"}]
```
"""

Coulombic_Efficiency = """
Paragraph: The average Coulombic efficiency over cycles 10–200 was 99.92% for the baseline electrolyte, which increased to 99.96% with 2% FEC addition.
JSON: ```JSON
[{"value": 99.92, "unit": "%", "cycle_number": 0, "condition_id": "C001", "source_text": "The average Coulombic efficiency over cycles 10\u2013200 was 99.92%"},
{"value": 99.96, "unit": "%", "cycle_number": 0, "condition_id": "C001", "source_text": "increased to 99.96% with 2% FEC addition"}]
```
"""

Cycle_Life_80 = """
Paragraph: The cycle life (to 80% capacity) of the NCM811||SiC cell was 420 cycles at 1C and 25 °C, limited mainly by Si volume expansion rather than electrolyte degradation.
JSON: ```JSON
[{"value": 420, "unit": "cycles", "condition_id": "C001", "source_text": "The cycle life (to 80% capacity) was 420 cycles at 1C and 25 \u00b0C"}]
```
"""

Energy_Density = """
Paragraph: The full pouch cell (NCM811||graphite) delivered a gravimetric energy density of 265 Wh/kg and a volumetric energy density of 675 Wh/L at 0.2C discharge, based on total cell weight and volume.
JSON: ```JSON
[{"value": {"gravimetric": {"value": 265, "unit": "Wh/kg"}, "volumetric": {"value": 675, "unit": "Wh/L"}}, "basis": "full cell", "condition_id": "C001", "source_text": "The full cell delivered a gravimetric energy density of 265 Wh/kg and a volumetric energy density of 675 Wh/L at 0.2C discharge"}]
```
"""

Maximum_Thermal_Runaway_Temperature = """
Paragraph: ARC testing of the 18650 cell at 100% SOC showed a maximum thermal runaway temperature of 812 °C, with the onset temperature at 187 °C.
JSON: ```JSON
[{"value": 812, "unit": "\u00b0C", "soc_percent": 100, "source_text": "ARC testing showed a maximum thermal runaway temperature of 812 \u00b0C at 100% SOC"}]
```
"""

Self_Heating_Onset_Temperature = """
Paragraph: The self-heating onset temperature (T_onset) of the NCM811 cell with the baseline electrolyte was 178 °C, which increased to 205 °C when 5% TMP was added as a flame retardant.
JSON: ```JSON
[{"value": 178, "unit": "\u00b0C", "soc_percent": 100, "source_text": "The self-heating onset temperature was 178 \u00b0C for the baseline electrolyte"},
{"value": 205, "unit": "\u00b0C", "soc_percent": 100, "source_text": "increased to 205 \u00b0C with 5% TMP"}]
```
"""

Gas_Evolution_Amount = """
Paragraph: DEMS measurement during the first charge to 4.3 V detected CO₂ evolution of 125 nmol/mg and H₂ evolution of 18 nmol/mg from the NMC811 cathode.
JSON: ```JSON
[{"value": {"CO2": 125, "H2": 18}, "unit": "nmol/mg", "condition_id": "C001", "method": "DEMS", "source_text": "DEMS measurement detected CO\u2082 evolution of 125 nmol/mg and H\u2082 evolution of 18 nmol/mg"}]
```
"""

Voltage_Hysteresis = """
Paragraph: At 1C and 50% SOC, the voltage hysteresis of the LiFePO₄||graphite cell was only 0.08 V, while the NCM811||graphite cell exhibited 0.15 V hysteresis.
JSON: ```JSON
[{"value": 0.08, "unit": "V", "condition_id": "C001", "source_text": "At 1C and 50% SOC, the voltage hysteresis of the LiFePO\u2084||graphite cell was 0.08 V"},
{"value": 0.15, "unit": "V", "condition_id": "C001", "source_text": "while the NCM811||graphite cell exhibited 0.15 V hysteresis"}]
```
"""

Transition_Metal_Dissolution_Concentration = """
Paragraph: ICP-MS analysis of the electrolyte after 300 cycles at 45 °C gave dissolved Mn: 85 mg/L, Ni: 32 mg/L, and Co: 11 mg/L, confirming severe cathode degradation at elevated temperature.
JSON: ```JSON
[{"value": {"Mn": 85, "Ni": 32, "Co": 11}, "unit": "mg/L", "cycle_number": 300, "method": "ICP-MS", "source_text": "ICP-MS analysis gave dissolved Mn: 85 mg/L, Ni: 32 mg/L, and Co: 11 mg/L after 300 cycles at 45 \u00b0C"}]
```
"""

Rate_Capability = """
Paragraph: The rate capability test at 25 °C (2.8-4.3 V) showed discharge capacities of 195, 185, 172, 148, and 112 mAh/g at 0.2C, 0.5C, 1C, 2C, and 5C, respectively.
JSON: ```JSON
[{"value": [{"rate": "0.2C", "capacity": 195, "unit": "mAh/g"}, {"rate": "0.5C", "capacity": 185, "unit": "mAh/g"}, {"rate": "1C", "capacity": 172, "unit": "mAh/g"}, {"rate": "2C", "capacity": 148, "unit": "mAh/g"}, {"rate": "5C", "capacity": 112, "unit": "mAh/g"}], "condition_id": "C001", "source_text": "The rate capability test showed discharge capacities of 195, 185, 172, 148, and 112 mAh/g at 0.2C, 0.5C, 1C, 2C, and 5C, respectively"}]
```
"""

DCIR = """
Paragraph: The DC internal resistance measured by a 10 s pulse at 1C and 50% SOC was 42 mΩ for the fresh cell, which increased to 78 mΩ after 500 cycles at 45 °C.
JSON: ```JSON
[{"value": 42, "unit": "m\u03a9", "pulse_duration_s": 10, "soc_percent": 50, "temperature_C": 25, "source_text": "The DCIR measured by a 10 s pulse at 1C and 50% SOC was 42 m\u03a9 for the fresh cell"},
{"value": 78, "unit": "m\u03a9", "pulse_duration_s": 10, "soc_percent": 50, "temperature_C": 25, "source_text": "increased to 78 m\u03a9 after 500 cycles at 45 \u00b0C"}]
```
"""

Thermal_Conductivity = """
Paragraph: The thermal conductivity of the 1 M LiPF6 in EC/DMC electrolyte was measured as 0.21 W/(m·K) at 25 °C using the transient hot-wire method.
JSON: ```JSON
[{"value": 0.21, "unit": "W/(m·K)", "temperature": "25 °C", "method": "transient hot-wire", "source_text": "The thermal conductivity of the 1 M LiPF6 in EC/DMC electrolyte was measured as 0.21 W/(m·K) at 25 °C"}]
```
"""

Thermal_Diffusivity = """
Paragraph: Laser flash analysis gave a thermal diffusivity of 0.089 mm²/s for the 1 M LiTFSI in DME/DOL electrolyte at room temperature.
JSON: ```JSON
[{"value": 0.089, "unit": "mm²/s", "temperature": "25 °C", "method": "laser flash analysis", "source_text": "Laser flash analysis gave a thermal diffusivity of 0.089 mm²/s"}]
```
"""

Specific_Surface_Area = """
Paragraph: Nitrogen adsorption measurements revealed a specific surface area of 8.5 m²/g for the carbon-coated LiFePO4 powder.
JSON: ```JSON
[{"value": 8.5, "unit": "m²/g", "method": "BET N2 adsorption", "source_text": "Nitrogen adsorption measurements revealed a specific surface area of 8.5 m²/g"}]
```
"""
