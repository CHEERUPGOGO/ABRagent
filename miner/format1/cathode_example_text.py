# -*- coding: utf-8 -*-
"""
正极材料提取示例
每个标签包含示例段落及对应的 JSON 输出，遵循 information 和 structured_data 定义。
"""

# ==================== Agent 1: 材料本征属性 ====================
Lattice_Parameters = """
Paragraph: The pristine LiNi₀.₈Co₀.₁Mn₀.₁O₂ cathode material crystallizes in the hexagonal system with refined lattice parameters a = b = 2.872 Å, c = 14.215 Å, α = β = 90°, γ = 120°, as determined by Rietveld refinement of powder XRD data.
JSON: ```JSON
[{"value": {"a": 2.872, "b": 2.872, "c": 14.215, "alpha": 90, "beta": 90, "gamma": 120}, "unit": "Å / °", "crystal_system": "hexagonal", "state": "pristine", "method": "XRD Rietveld refinement", "source_text": "The pristine LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 cathode material crystallizes in the hexagonal system with refined lattice parameters a = b = 2.872 \u00c5, c = 14.215 \u00c5..."}]
```
"""

Crystal_Space_Group = """
Paragraph: XRD analysis confirmed that the layered LiNi₀.₆Mn₀.₂Co₀.₂O₂ cathode adopts the α-NaFeO₂ structure with space group R-3m (No. 166).
JSON: ```JSON
[{"value": "R-3m", "unit": "", "method": "XRD", "source_text": "XRD analysis confirmed that the layered LiNi\u2080.\u2086Mn\u2080.\u2082Co\u2080.\u2082O\u2082 cathode adopts the \u03b1-NaFeO\u2082 structure with space group R-3m (No. 166)."}]
```
"""

Lithium_Ion_Diffusion_Activation_Energy = """
Paragraph: DFT calculations using the NEB method predicted a lithium-ion diffusion activation energy of 0.28 eV for Li migration along the ab-plane in LiCoO₂, while temperature-dependent GITT measurements from 253 to 313 K gave an experimental value of 0.35 eV at 50% SOC.
JSON: ```JSON
[{"value": 0.28, "unit": "eV", "method": "DFT-NEB", "path": "ab-plane", "soc": "", "source_text": "DFT calculations using the NEB method predicted a lithium-ion diffusion activation energy of 0.28 eV for Li migration along the ab-plane in LiCoO\u2082"},
{"value": 0.35, "unit": "eV", "method": "GITT", "path": "", "soc": "50% SOC", "source_text": "temperature-dependent GITT measurements from 253 to 313 K gave an experimental value of 0.35 eV at 50% SOC"}]
```
"""

Li_Ion_Migration_Barrier = """
Paragraph: NEB calculations revealed that the Li migration barrier from the octahedral Li site to the adjacent octahedral site via the tetrahedral intermediate in LiNi₀.₅Mn₁.₅O₄ is 0.42 eV.
JSON: ```JSON
[{"value": 0.42, "unit": "eV", "path": "oct→tet→oct", "method": "DFT-NEB", "source_text": "NEB calculations revealed that the Li migration barrier from the octahedral Li site to the adjacent octahedral site via the tetrahedral intermediate in LiNi\u2080.\u2085Mn\u2081.\u2085O\u2084 is 0.42 eV."}]
```
"""

Electronic_Band_Gap = """
Paragraph: UV-vis diffuse reflectance spectroscopy gave an experimental band gap of 3.82 eV for LiFePO₄, consistent with its insulating behavior; DFT-PBE underestimated this value at 0.52 eV (indirect), while HSE06 yielded 1.05 eV (indirect).
JSON: ```JSON
[{"value": 3.82, "unit": "eV", "type": "indirect", "method": "UV-vis DRS", "source_text": "UV-vis diffuse reflectance spectroscopy gave an experimental band gap of 3.82 eV for LiFePO\u2084"},
{"value": 0.52, "unit": "eV", "type": "indirect", "method": "DFT-PBE", "source_text": "DFT-PBE underestimated this value at 0.52 eV (indirect)"},
{"value": 1.05, "unit": "eV", "type": "indirect", "method": "HSE06", "source_text": "HSE06 yielded 1.05 eV (indirect)"}]
```
"""

Theoretical_Specific_Capacity = """
Paragraph: Based on the Ni²⁺/Ni⁴⁺ two-electron redox couple (n=2 per formula unit), the theoretical specific capacity of LiNi₀.₈Co₀.₁Mn₀.₁O₂ is calculated to be 275 mAh/g.
JSON: ```JSON
[{"value": 275, "unit": "mAh/g", "n_electrons": 2, "redox_reaction": "Ni\u00b2\u207a/Ni\u2074\u207a", "source_text": "Based on the Ni\u00b2\u207a/Ni\u2074\u207a two-electron redox couple (n=2 per formula unit), the theoretical specific capacity of LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 is 275 mAh/g."}]
```
"""

Formation_Energy = """
Paragraph: DFT+U calculations predicted the formation energy of spinel LiNi₀.₅Mn₁.₅O₄ to be -2.34 eV per formula unit relative to the constituent oxides Li₂O, NiO, and MnO₂, indicating strong thermodynamic stability.
JSON: ```JSON
[{"value": -2.34, "unit": "eV/f.u.", "method": "DFT+U", "source_text": "DFT+U calculations predicted the formation energy of spinel LiNi\u2080.\u2085Mn\u2081.\u2085O\u2084 to be -2.34 eV per formula unit relative to the constituent oxides Li\u2082O, NiO, and MnO\u2082"}]
```
"""

Volume_Change_Ratio = """
Paragraph: The unit cell volume of LiNi₀.₈Co₀.₁Mn₀.₁O₂ decreased from 102.1 Å³ in the fully lithiated state to 97.3 Å³ upon full delithiation to Li₀.₀, corresponding to a volume change ratio of -4.7%.
JSON: ```JSON
[{"value": -4.7, "unit": "%", "from_state": "fully lithiated (Li\u2081.\u2080)", "to_state": "fully delithiated (Li\u2080.\u2080)", "source_text": "The unit cell volume decreased from 102.1 \u00c5\u00b3 in the fully lithiated state to 97.3 \u00c5\u00b3 upon full delithiation to Li\u2080.\u2080, corresponding to a volume change ratio of -4.7%."}]
```
"""

a_c_axis_expansion = """
Paragraph: In-situ XRD during delithiation of LiNiO₂ showed an anisotropic lattice response: the a-axis contracted by Δa = -0.13 Å (-4.6%) and the c-axis expanded by Δc = +0.28 Å (+2.1%) upon charging to 4.3 V vs. Li/Li⁺, resulting in a total volume expansion of ΔV = +1.3%.
JSON: ```JSON
[{"value": {"delta_a": -0.13, "delta_c": 0.28, "delta_V": 1.3}, "unit": "Å / Å / %", "from_state": "pristine", "to_state": "charged to 4.3 V vs. Li/Li\u207a", "source_text": "In-situ XRD during delithiation of LiNiO\u2082 showed an anisotropic lattice response: the a-axis contracted by \u0394a = -0.13 \u00c5 (-4.6%) and the c-axis expanded by \u0394c = +0.28 \u00c5 (+2.1%) upon charging to 4.3 V vs. Li/Li\u207a, resulting in a total volume expansion of \u0394V = +1.3%."}]
```
"""

Oxygen_Vacancy_Concentration = """
Paragraph: Thermogravimetric analysis of Li-rich Li₁.₂Mn₀.₅₄Ni₀.₁₃Co₀.₁₃O₂ indicated an oxygen deficiency of δ = 0.07 in Li₁.₂Mn₀.₅₄Ni₀.₁₃Co₀.₁₃O₂₋δ, attributed to surface oxygen loss during high-temperature synthesis.
JSON: ```JSON
[{"value": 0.07, "unit": "mole fraction", "method": "TGA", "source_text": "Thermogravimetric analysis of Li-rich Li\u2081.\u2082Mn\u2080.\u2085\u2084Ni\u2080.\u2081\u2083Co\u2080.\u2081\u2083O\u2082 indicated an oxygen deficiency of \u03b4 = 0.07 in Li\u2081.\u2082Mn\u2080.\u2085\u2084Ni\u2080.\u2081\u2083Co\u2080.\u2081\u2083O\u2082\u208b\u03b4"}]
```
"""

Oxygen_Vacancy_Formation_Energy = """
Paragraph: DFT+U calculations revealed that the oxygen vacancy formation energy at the (003) surface of LiCoO₂ is 2.45 eV, substantially lower than the bulk value of 3.98 eV, explaining the preferential oxygen release from the surface region.
JSON: ```JSON
[{"value": 2.45, "unit": "eV", "method": "DFT+U", "source_text": "DFT+U calculations revealed that the oxygen vacancy formation energy at the (003) surface of LiCoO\u2082 is 2.45 eV"},
{"value": 3.98, "unit": "eV", "method": "DFT+U", "source_text": "substantially lower than the bulk value of 3.98 eV"}]
```
"""

Transition_Metal_Migration_Energy_Barrier = """
Paragraph: The CI-NEB method determined the Ni²⁺ migration barrier from the transition metal layer to the Li layer in LiNi₀.₈Co₀.₁Mn₀.₁O₂ to be 1.28 eV, whereas Co³⁺ exhibited a higher barrier of 1.76 eV, consistent with the preferential Ni/Li antisite disorder.
JSON: ```JSON
[{"value": 1.28, "unit": "eV", "element": "Ni", "path": "TM layer \u2192 Li layer", "method": "DFT-CI-NEB", "source_text": "The CI-NEB method determined the Ni\u00b2\u207a migration barrier from the transition metal layer to the Li layer in LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 to be 1.28 eV"},
{"value": 1.76, "unit": "eV", "element": "Co", "path": "TM layer \u2192 Li layer", "method": "DFT-CI-NEB", "source_text": "Co\u00b3\u207a exhibited a higher barrier of 1.76 eV"}]
```
"""

Interlayer_Spacing_of_TM_Layers = """
Paragraph: Rietveld refinement of synchrotron XRD data gave the transition metal layer interlayer spacing (d₀₀₃) of O3-type LiNi₀.₅Mn₀.₅O₂ as 4.71 Å in the pristine state and 4.86 Å after 50% delithiation.
JSON: ```JSON
[{"value": 4.71, "unit": "Å", "plane": "003", "state": "pristine", "source_text": "Rietveld refinement of synchrotron XRD data gave the transition metal layer interlayer spacing (d\u2080\u2080\u2083) of O3-type LiNi\u2080.\u2085Mn\u2080.\u2085O\u2082 as 4.71 \u00c5 in the pristine state"},
{"value": 4.86, "unit": "Å", "plane": "003", "state": "50% delithiated", "source_text": "and 4.86 \u00c5 after 50% delithiation"}]
```
"""

Density_of_States_at_Fermi_Level = """
Paragraph: DFT-PBE calculations showed that the density of states at the Fermi level for delithiated Li₀.₅CoO₂ is 2.85 states/eV per formula unit, confirming the metallic nature of the charged state.
JSON: ```JSON
[{"value": 2.85, "unit": "states/eV/f.u.", "method": "DFT-PBE", "source_text": "DFT-PBE calculations showed that the density of states at the Fermi level for delithiated Li\u2080.\u2085CoO\u2082 is 2.85 states/eV per formula unit"}]
```
"""

Bader_Charge = """
Paragraph: Bader charge analysis of LiNi₀.₈Co₀.₁Mn₀.₁O₂ gave effective charges of +1.38 e for Ni, +1.52 e for Mn, and +1.25 e for Co, indicating significant ionicity of the Ni-O bond.
JSON: ```JSON
[{"value": {"Ni": 1.38, "Mn": 1.52, "Co": 1.25}, "unit": "e", "method": "DFT-VASP", "source_text": "Bader charge analysis of LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 gave effective charges of +1.38 e for Ni, +1.52 e for Mn, and +1.25 e for Co"}]
```
"""

Chemical_Composition_Mole_Fractions = """
Paragraph: ICP-OES analysis confirmed the target composition of the synthesized cathode material as Li₁.₀₂Ni₀.₇₉Co₀.₁₁Mn₀.₁₀O₂, corresponding to Li:Ni:Co:Mn molar ratios of 1.02:0.79:0.11:0.10.
JSON: ```JSON
[{"value": {"Li": 1.02, "Ni": 0.79, "Co": 0.11, "Mn": 0.10}, "unit": "mole fraction", "method": "ICP-OES", "source_text": "ICP-OES analysis confirmed the target composition as Li\u2081.\u2080\u2082Ni\u2080.\u2087\u2089Co\u2080.\u2081\u2081Mn\u2080.\u2081\u2080O\u2082, corresponding to Li:Ni:Co:Mn molar ratios of 1.02:0.79:0.11:0.10"}]
```
"""

Element_Valence_State = """
Paragraph: XPS Ni 2p₃/₂ spectra of as-synthesized LiNi₀.₈Co₀.₁Mn₀.₁O₂ revealed a mixture of Ni²⁺ (25%) and Ni³⁺ (75%), while Co remained exclusively as Co³⁺ and Mn as Mn⁴⁺.
JSON: ```JSON
[{"value": {"Ni": "+2/+3", "Co": "+3", "Mn": "+4"}, "unit": "oxidation state", "state": "as-synthesized", "method": "XPS", "source_text": "XPS Ni 2p\u2083/\u2082 spectra of as-synthesized LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 revealed a mixture of Ni\u00b2\u207a (25%) and Ni\u00b3\u207a (75%), while Co remained exclusively as Co\u00b3\u207a and Mn as Mn\u2074\u207a"}]
```
"""

Jahn_Teller_Active_Ion_Content = """
Paragraph: From the Mn 2p XPS fitting, the Jahn-Teller active Mn³⁺ content in the LiNi₀.₅Mn₁.₅O₄ spinel was determined to be 0.28 molar fraction, with the remainder as inactive Mn⁴⁺.
JSON: ```JSON
[{"value": 0.28, "unit": "mole fraction", "derived": false, "source_text": "From the Mn 2p XPS fitting, the Jahn-Teller active Mn\u00b3\u207a content in the LiNi\u2080.\u2085Mn\u2081.\u2085O\u2084 spinel was determined to be 0.28 molar fraction"}]
```
"""

devtE = """
Paragraph: The mean absolute deviation of total valence electrons (devtE) for the high-entropy cathode LiNi₀.₂Co₀.₂Mn₀.₂Fe₀.₂Al₀.₂O₂ was calculated from the composition to be 0.91.
JSON: ```JSON
[{"value": 0.91, "unit": "dimensionless", "derived": true, "method": "formula_based", "source_text": "The mean absolute deviation of total valence electrons (devtE) for the high-entropy cathode LiNi\u2080.\u2082Co\u2080.\u2082Mn\u2080.\u2082Fe\u2080.\u2082Al\u2080.\u2082O\u2082 was calculated from the composition to be 0.91."}]
```
"""

VEd = """
Paragraph: Considering the average valence states from XANES (Ni².⁸⁺, Co³⁺, Mn⁴⁺), the weighted average d-orbital electron count VEd of LiNi₀.₈Co₀.₁Mn₀.₁O₂ was computed to be 7.6.
JSON: ```JSON
[{"value": 7.6, "unit": "dimensionless", "derived": true, "method": "formula_based", "source_text": "Considering the average valence states from XANES (Ni\u00b2.\u2078\u207a, Co\u00b3\u207a, Mn\u2074\u207a), the weighted average d-orbital electron count VEd of LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 was computed to be 7.6."}]
```
"""

Average_Electron_Affinity = """
Paragraph: Using elemental data from the Mendeleev database, the composition-weighted average electron affinity of the metals in LiNi₀.₈Co₀.₁Mn₀.₁O₂ was calculated to be 0.94 eV.
JSON: ```JSON
[{"value": 0.94, "unit": "eV", "derived": true, "method": "elemental_database", "source_text": "Using elemental data from the Mendeleev database, the composition-weighted average electron affinity of the metals in LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 was calculated to be 0.94 eV."}]
```
"""

Average_Deviation_of_Ionic_Radius = """
Paragraph: Based on Shannon radii for CN=6, the average deviation of ionic radii for the transition metals in LiNi₀.₈Co₀.₁Mn₀.₁O₂ was computed to be 9.5 pm, reflecting significant size mismatch.
JSON: ```JSON
[{"value": 9.5, "unit": "pm", "derived": true, "method": "formula_based", "source_text": "Based on Shannon radii for CN=6, the average deviation of ionic radii for the transition metals in LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 was computed to be 9.5 pm"}]
```
"""

Average_Ionization_Energy = """
Paragraph: The first ionization energy, averaged over Li, Ni, Co, and Mn in LiNi₀.₈Co₀.₁Mn₀.₁O₂, gave a value of 7.38 eV using NIST elemental data.
JSON: ```JSON
[{"value": 7.38, "unit": "eV", "derived": true, "method": "elemental_database", "source_text": "The first ionization energy, averaged over Li, Ni, Co, and Mn in LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082, gave a value of 7.38 eV using NIST elemental data."}]
```
"""

Configurational_Entropy = """
Paragraph: The configurational entropy of the high-entropy layered cathode Li(Ni₀.₂Co₀.₂Mn₀.₂Fe₀.₂Ti₀.₂)O₂ was calculated to be S_config = 1.61R based on ideal mixing of five transition metal species on the TM sites.
JSON: ```JSON
[{"value": 1.61, "unit": "R", "derived": true, "method": "formula_based", "source_text": "The configurational entropy of the high-entropy layered cathode Li(Ni\u2080.\u2082Co\u2080.\u2082Mn\u2080.\u2082Fe\u2080.\u2082Ti\u2080.\u2082)O\u2082 was calculated to be S_config = 1.61R based on ideal mixing of five TM species."}]
```
"""

Valence_Electron_Count = """
Paragraph: Nickel in the +2 oxidation state has a valence electron count of 8 (3d⁸ configuration), while Co³⁺ has 6 valence electrons (3d⁶).
JSON: ```JSON
[{"value": {"Ni": 8, "Co": 6}, "unit": "dimensionless", "derived": true, "source_text": "Nickel in the +2 oxidation state has a valence electron count of 8 (3d\u2078 configuration), while Co\u00b3\u207a has 6 valence electrons (3d\u2076)."}]
```
"""

d_Electron_Configuration_Type = """
Paragraph: The incorporation of d⁰ Ti⁴⁺ and d¹⁰ Zn²⁺ into LiNi₀.₈Co₀.₁Mn₀.₁O₂ effectively suppresses Jahn-Teller distortion by diluting the active Ni³⁺ (d⁷) ions.
JSON: ```JSON
[{"value": "d\u2070", "unit": "", "derived": true, "source_text": "The incorporation of d\u2070 Ti\u2074\u207a and d\u00b9\u2070 Zn\u00b2\u207a into LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 effectively suppresses Jahn-Teller distortion by diluting the active Ni\u00b3\u207a (d\u2077) ions."}]
```
"""

Li_Ni_mixing_ratio = """
Paragraph: Rietveld refinement of neutron diffraction data gave a Li/Ni antisite mixing ratio of 3.8% in the as-synthesized LiNi₀.₈Co₀.₁Mn₀.₁O₂, which increased to 9.2% after 100 cycles at 1C.
JSON: ```JSON
[{"value": 3.8, "unit": "%", "method": "neutron diffraction Rietveld", "state": "as-synthesized", "source_text": "Rietveld refinement of neutron diffraction data gave a Li/Ni antisite mixing ratio of 3.8% in the as-synthesized LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082"},
{"value": 9.2, "unit": "%", "method": "neutron diffraction Rietveld", "state": "after 100 cycles at 1C", "source_text": "which increased to 9.2% after 100 cycles at 1C"}]
```
"""

Metal_Oxygen_Bond_Energy = """
Paragraph: DFT-COHP analysis revealed that the Ni-O bond dissociation energy in LiNi₀.₅Mn₁.₅O₄ is 3.92 eV, while the Mn-O bond energy is 3.18 eV, indicating stronger Ni-O covalency.
JSON: ```JSON
[{"value": 3.92, "unit": "eV", "bond_type": "Ni-O", "method": "DFT-COHP", "source_text": "DFT-COHP analysis revealed that the Ni-O bond dissociation energy in LiNi\u2080.\u2085Mn\u2081.\u2085O\u2084 is 3.92 eV"},
{"value": 3.18, "unit": "eV", "bond_type": "Mn-O", "method": "DFT-COHP", "source_text": "while the Mn-O bond energy is 3.18 eV"}]
```
"""

Primary_Particle_Size_Distribution = """
Paragraph: SEM image analysis of the single-crystal LiNi₀.₈Co₀.₁Mn₀.₁O₂ powder gave a primary particle size distribution of D10 = 120 nm, D50 = 210 nm, and D90 = 380 nm.
JSON: ```JSON
[{"value": {"D10": 120, "D50": 210, "D90": 380}, "unit": "nm", "method": "SEM image analysis", "source_text": "SEM image analysis of the single-crystal LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 powder gave a primary particle size distribution of D10 = 120 nm, D50 = 210 nm, and D90 = 380 nm."}]
```
"""

Secondary_Particle_Size_Distribution = """
Paragraph: Laser diffraction of the polycrystalline NCM811 powder showed a secondary particle D50 of 9.8 μm with a span of 1.3.
JSON: ```JSON
[{"value": {"D50": 9.8}, "unit": "μm", "method": "laser diffraction", "source_text": "Laser diffraction of the polycrystalline NCM811 powder showed a secondary particle D50 of 9.8 \u03bcm with a span of 1.3."}]
```
"""

Electrode_Pore_Size_Distribution = """
Paragraph: Mercury intrusion porosimetry of the calendered NCM811 cathode revealed a bimodal pore distribution: mesopores at 35 nm (65% of total pore volume) and macropores at 280 nm (35%).
JSON: ```JSON
[{"value": {"mesopore_vol%": 65, "macropore_vol%": 35}, "unit": "vol%", "method": "MIP", "source_text": "Mercury intrusion porosimetry of the calendered NCM811 cathode revealed a bimodal pore distribution: mesopores at 35 nm (65% of total pore volume) and macropores at 280 nm (35%)."}]
```
"""

Surface_Spinel_Layer_Thickness = """
Paragraph: HAADF-STEM with FFT analysis of the cycled LiNi₀.₈Co₀.₁Mn₀.₁O₂ particle showed a surface spinel reconstruction layer of approximately 5.2 nm thickness after 200 cycles at 45 °C.
JSON: ```JSON
[{"value": 5.2, "unit": "nm", "state": "after 200 cycles at 45 \u00b0C", "method": "HAADF-STEM + FFT", "source_text": "HAADF-STEM with FFT analysis of the cycled LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 particle showed a surface spinel reconstruction layer of approximately 5.2 nm thickness after 200 cycles at 45 \u00b0C."}]
```
"""

XPS_ROCO2Li_Peak = """
Paragraph: C 1s XPS spectrum of the NCM811 cathode after 50 cycles exhibited a peak at 290.3 eV (ROCO₂Li) comprising 12.5% of the total C 1s area, indicating substantial electrolyte oxidation.
JSON: ```JSON
[{"value": 12.5, "unit": "at%", "state": "after 50 cycles", "source_text": "C 1s XPS spectrum of the NCM811 cathode after 50 cycles exhibited a peak at 290.3 eV (ROCO\u2082Li) comprising 12.5% of the total C 1s area"}]
```
"""

XPS_C_O_Peak = """
Paragraph: The C 1s spectrum also contained a C-O peak at 286.6 eV (8.3% of total carbon), assigned to polyether species from DOL polymerization.
JSON: ```JSON
[{"value": 8.3, "unit": "at%", "state": "after 50 cycles", "source_text": "The C 1s spectrum also contained a C-O peak at 286.6 eV (8.3% of total carbon) assigned to polyether species from DOL polymerization."}]
```
"""

XPS_NiF2_Peak = """
Paragraph: After 200 cycles at 45 °C, the Ni 2p XPS spectrum of the NCM811 cathode revealed a new peak at 857.1 eV characteristic of NiF₂, confirming HF-induced surface corrosion.
JSON: ```JSON
[{"value": "present", "unit": "detected", "state": "after 200 cycles at 45 \u00b0C", "source_text": "After 200 cycles at 45 \u00b0C, the Ni 2p XPS spectrum of the NCM811 cathode revealed a new peak at 857.1 eV characteristic of NiF\u2082"}]
```
"""

LixPOyFz = """
Paragraph: F 1s depth profiling of the CEI showed a peak at 687.4 eV assigned to LiₓPOyFz species, whose intensity increased from 3% at the surface to 9% at 15 nm depth.
JSON: ```JSON
[{"value": 9, "unit": "at%", "state": "after formation at 15 nm depth", "source_text": "F 1s depth profiling of the CEI showed a peak at 687.4 eV assigned to Li\u2093POyFz species, whose intensity increased from 3% at the surface to 9% at 15 nm depth."}]
```
"""


# ==================== Agent 2: 电化学性能 ====================

Electronic_Conductivity_Bulk = """
Paragraph: The bulk electronic conductivity of the LiFePO₄/C composite pellet was measured by the four-point probe method to be 0.85 S/cm, two orders of magnitude higher than that of pristine LiFePO₄ (0.008 S/cm).
JSON: ```JSON
[{"value": 0.85, "unit": "S/cm", "method": "four-point probe", "source_text": "The bulk electronic conductivity of the LiFePO\u2084/C composite pellet was measured by the four-point probe method to be 0.85 S/cm"}]
```
"""

Initial_Coulombic_Efficiency = """
Paragraph: The NCM811 || graphite full cell displayed an initial Coulombic efficiency of 88.5% at 0.1C between 2.8 and 4.2 V (25 °C), with the irreversible capacity mainly consumed by SEI formation on the anode.
JSON: ```JSON
[{"value": 88.5, "unit": "%", "condition_id": "C001", "source_text": "The NCM811 || graphite full cell displayed an initial Coulombic efficiency of 88.5% at 0.1C between 2.8 and 4.2 V (25 \u00b0C)"}]
```
"""

Discharge_Specific_Capacity_Initial = """
Paragraph: The LiNi₀.₈Co₀.₁Mn₀.₁O₂ cathode delivered an initial discharge specific capacity of 202.3 mAh/g at 0.1C (2.8-4.3 V vs. Li/Li⁺) in the first cycle.
JSON: ```JSON
[{"value": 202.3, "unit": "mAh/g", "condition_id": "C001", "source_text": "The LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 cathode delivered an initial discharge specific capacity of 202.3 mAh/g at 0.1C (2.8-4.3 V vs. Li/Li\u207a) in the first cycle."}]
```
"""

Rate_Performance = """
Paragraph: Excellent rate capability was observed for the LFP cathode, with a capacity ratio of 0.78 between 10C and 0.2C (10C: 124 mAh/g, 0.2C: 159 mAh/g).
JSON: ```JSON
[{"value": 0.78, "unit": "ratio", "condition_id": "C001", "high_rate": "10C", "low_rate": "0.2C", "source_text": "Excellent rate capability was observed for the LFP cathode, with a capacity ratio of 0.78 between 10C and 0.2C (10C: 124 mAh/g, 0.2C: 159 mAh/g)."}]
```
"""

Capacity_Retention_Ratio = """
Paragraph: After 500 cycles at 1C (25 °C, 2.8-4.3 V), the NCM811 cathode retained 89.7% of its initial discharge capacity, demonstrating stable cycling performance.
JSON: ```JSON
[{"value": 89.7, "unit": "%", "condition_id": "C001", "cycle_number": 500, "source_text": "After 500 cycles at 1C (25 \u00b0C, 2.8-4.3 V), the NCM811 cathode retained 89.7% of its initial discharge capacity"}]
```
"""

Rate_Capability_Profile = """
Paragraph: The rate capability of the LFP cathode at 25 °C (2.5-3.8 V) was: 0.2C → 160 mAh/g, 0.5C → 155, 1C → 148, 5C → 132, 10C → 118, 20C → 95 mAh/g.
JSON: ```JSON
[{"value": [{"rate": "0.2C", "capacity": 160}, {"rate": "0.5C", "capacity": 155}, {"rate": "1C", "capacity": 148}, {"rate": "5C", "capacity": 132}, {"rate": "10C", "capacity": 118}, {"rate": "20C", "capacity": 95}], "unit": "mAh/g", "condition_id": "C001", "source_text": "The rate capability of the LFP cathode at 25 \u00b0C (2.5-3.8 V) was: 0.2C \u2192 160 mAh/g, 0.5C \u2192 155, 1C \u2192 148, 5C \u2192 132, 10C \u2192 118, 20C \u2192 95 mAh/g."}]
```
"""

Nominal_Discharge_Voltage = """
Paragraph: The nominal discharge voltage of the LiFePO₄ cathode is 3.45 V vs. Li/Li⁺, taken as the midpoint of the flat voltage plateau.
JSON: ```JSON
[{"value": 3.45, "unit": "V", "condition_id": "C001", "reference": "vs. Li/Li\u207a", "source_text": "The nominal discharge voltage of the LiFePO\u2084 cathode is 3.45 V vs. Li/Li\u207a, taken as the midpoint of the flat voltage plateau."}]
```
"""

Average_Discharge_Voltage = """
Paragraph: The integrated average discharge voltage of LiNi₀.₈Co₀.₁Mn₀.₁O₂ was calculated to be 3.82 V (vs. Li/Li⁺) at 0.1C between 2.8 and 4.3 V.
JSON: ```JSON
[{"value": 3.82, "unit": "V", "condition_id": "C001", "reference": "vs. Li/Li\u207a", "source_text": "The integrated average discharge voltage of LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 was calculated to be 3.82 V (vs. Li/Li\u207a) at 0.1C between 2.8 and 4.3 V."}]
```
"""

Charge_Discharge_Voltage_Gap = """
Paragraph: The voltage hysteresis of the LiNi₀.₈Co₀.₁Mn₀.₁O₂ cathode at 50% SOC was measured to be 0.11 V at 1C (25 °C).
JSON: ```JSON
[{"value": 0.11, "unit": "V", "condition_id": "C001", "soc": 50, "source_text": "The voltage hysteresis of the LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 cathode at 50% SOC was measured to be 0.11 V at 1C (25 \u00b0C)."}]
```
"""

Ion_Diffusion_Coefficient = """
Paragraph: GITT measurements at 3.8 V (vs. Li/Li⁺) gave a lithium chemical diffusion coefficient D_Li of 2.1 × 10⁻¹¹ cm²/s for LiNi₀.₈Co₀.₁Mn₀.₁O₂ at 25 °C.
JSON: ```JSON
[{"value": "2.1e-11", "unit": "cm2/s", "condition_id": "C001", "method": "GITT", "voltage_V": 3.8, "source_text": "GITT measurements at 3.8 V (vs. Li/Li\u207a) gave a lithium chemical diffusion coefficient D_Li of 2.1 \u00d7 10\u207b\u00b9\u00b9 cm\u00b2/s for LiNi\u2080.\u2088Co\u2080.\u2081Mn\u2080.\u2081O\u2082 at 25 \u00b0C."}]
```
"""

Gravimetric_Energy_Density = """
Paragraph: The NCM811 || graphite pouch cell delivered a gravimetric energy density of 268 Wh/kg at 0.2C discharge based on total cell mass, including current collectors, separator, electrolyte, and packaging.
JSON: ```JSON
[{"value": 268, "unit": "Wh/kg", "condition_id": "C002", "basis": "full_cell", "source_text": "The NCM811 || graphite pouch cell delivered a gravimetric energy density of 268 Wh/kg at 0.2C discharge based on total cell mass"}]
```
"""

Volumetric_Energy_Density = """
Paragraph: The same cell achieved a volumetric energy density of 692 Wh/L, calculated from the external dimensions of the pouch.
JSON: ```JSON
[{"value": 692, "unit": "Wh/L", "condition_id": "C002", "basis": "full_cell", "source_text": "The same cell achieved a volumetric energy density of 692 Wh/L, calculated from the external dimensions of the pouch."}]
```
"""

Charge_Transfer_Resistance = """
Paragraph: EIS of the NCM811 cathode at 50% SOC (3.8 V vs. Li/Li⁺, 25 °C) gave a charge transfer resistance Rct of 38.2 Ω, fitted with an equivalent circuit of R(RQ)(RQ).
JSON: ```JSON
[{"value": 38.2, "unit": "Ω", "condition_id": "C001", "cycle_number": 0, "soc_percent": 50, "source_text": "EIS of the NCM811 cathode at 50% SOC (3.8 V vs. Li/Li\u207a, 25 \u00b0C) gave a charge transfer resistance Rct of 38.2 \u03a9"}]
```
"""

SEI_Resistance = """
Paragraph: After 100 cycles at 1C, EIS of the NCM811 cathode at 50% SOC (25 °C) showed a surface film resistance R_SEI of 12.5 Ω, separated from the charge transfer semicircle by DRT.
JSON: ```JSON
[{"value": 12.5, "unit": "Ω", "condition_id": "C001", "cycle_number": 100, "soc_percent": 50, "source_text": "EIS of the NCM811 cathode showed a surface film resistance R_SEI of 12.5 Ω at 50% SOC"}]
```
"""

Self_Discharge_Rate = """
Paragraph: The NCM811 || graphite full cell stored at 100% SOC and 25 °C for 30 days exhibited a self-discharge rate of 2.9% per month.
JSON: ```JSON
[{"value": 2.9, "unit": "%/month", "condition_id": "C001", "soc_percent": 100, "source_text": "The NCM811 || graphite full cell stored at 100% SOC and 25 \u00b0C for 30 days exhibited a self-discharge rate of 2.9% per month."}]
```
"""

Thermal_Runaway_Onset_Temperature = """
Paragraph: ARC testing of the NCM811 || graphite 18650 cell at 100% SOC revealed a thermal runaway onset temperature T_on of 195 °C.
JSON: ```JSON
[{"value": 195, "unit": "°C", "method": "ARC", "soc_percent": 100, "source_text": "ARC testing of the NCM811 || graphite 18650 cell at 100% SOC revealed a thermal runaway onset temperature T_on of 195 \u00b0C."}]
```
"""

Phase_Transition_Voltage = """
Paragraph: In-situ XRD of LiNi₀.₈Co₀.₁Mn₀.₁O₂ during the first charge revealed phase transitions at voltages of 3.72 V (H1→M), 4.05 V (M→H2), and 4.22 V (H2→H3) vs. Li/Li⁺.
JSON: ```JSON
[{"value": 3.72, "unit": "V", "transition_name": "H1\u2192M", "condition_id": "C001", "source_text": "In-situ XRD revealed a phase transition at 3.72 V (H1\u2192M)"},
{"value": 4.05, "unit": "V", "transition_name": "M\u2192H2", "condition_id": "C001", "source_text": "4.05 V (M\u2192H2)"},
{"value": 4.22, "unit": "V", "transition_name": "H2\u2192H3", "condition_id": "C001", "source_text": "and 4.22 V (H2\u2192H3)"}]
```
"""

Transition_Metal_Dissolution_Amount = """
Paragraph: ICP-MS analysis of the electrolyte after 500 cycles at 45 °C showed Mn dissolution of 82 ppm, Ni of 34 ppm, and Co of 12 ppm from the NCM811 cathode.
JSON: ```JSON
[{"value": 82, "unit": "ppm", "element": "Mn", "condition_id": "C001", "method": "ICP-MS", "source_text": "ICP-MS analysis of the electrolyte after 500 cycles at 45 \u00b0C showed Mn dissolution of 82 ppm"},
{"value": 34, "unit": "ppm", "element": "Ni", "condition_id": "C001", "method": "ICP-MS", "source_text": "Ni of 34 ppm"},
{"value": 12, "unit": "ppm", "element": "Co", "condition_id": "C001", "method": "ICP-MS", "source_text": "and Co of 12 ppm"}]
```
"""

O2_CO2_Evolution = """
Paragraph: DEMS measurements of Li₁.₂Mn₀.₅₄Ni₀.₁₃Co₀.₁₃O₂ during first charge to 4.8 V detected O₂ evolution of 142 μmol/g (onset 4.48 V) and CO₂ evolution of 48 μmol/g (onset 4.65 V).
JSON: ```JSON
[{"value": {"O2": 142, "CO2": 48}, "unit": "μmol/g", "method": "DEMS", "condition_id": "C001", "source_text": "DEMS measurements of Li\u2081.\u2082Mn\u2080.\u2085\u2084Ni\u2080.\u2081\u2083Co\u2080.\u2081\u2083O\u2082 during first charge to 4.8 V detected O\u2082 evolution of 142 \u03bcmol/g (onset 4.48 V) and CO\u2082 evolution of 48 \u03bcmol/g (onset 4.65 V)."}]
```
"""


Adhesion_Strength = """
Paragraph: The 180° peel test showed that the NCM811 electrode exhibited an adhesion strength of 3.2 N/m to the aluminum current collector after calendering.
JSON: ```JSON
[{"value": 3.2, "unit": "N/m", "test_method": "180° peel test", "source_text": "The 180° peel test showed that the NCM811 electrode exhibited an adhesion strength of 3.2 N/m"}]
```
"""

Mesoscopic_Porosity = """
Paragraph: Mercury intrusion porosimetry of the calendered graphite anode revealed a mesoscopic porosity of 12.4% with a median pore diameter of 45 nm.
JSON: ```JSON
[{"value": 12.4, "unit": "%", "method": "mercury intrusion porosimetry", "source_text": "Mercury intrusion porosimetry of the calendered graphite anode revealed a mesoscopic porosity of 12.4%"}]
```
"""

