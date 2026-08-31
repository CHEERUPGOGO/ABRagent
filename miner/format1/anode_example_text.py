# -*- coding: utf-8 -*-
"""
负极材料提取示例文本
严格对照 anode_structured_data.py 的 schema 和 anode_information.py 的提取规则。
覆盖 Agent 1（材料本征属性，14标签）和 Agent 2（电化学性能，26标签）。
负极材料示例：石墨、硅、硅碳复合、锂金属、Sn、硬碳、SiOx、LTO 等。
"""

# ==================== Agent 1: 材料本征属性 (14 标签) ====================

Elemental_Composition = """
Paragraph: The silicon/carbon composite anode was composed of Si, C, O, and trace Al, as confirmed by EDS elemental mapping. The Si content was 65 wt%, C 30 wt%, and O 4.5 wt%, with Al detected at below 0.5 wt%.
JSON: ```JSON
[{"value": ["Si", "C", "O", "Al"], "unit": "", "method": "EDS", "source_text": "The silicon/carbon composite anode was composed of Si, C, O, and trace Al"}]
```
"""

Chemical_Formula = """
Paragraph: The anode active material was identified as Li4Ti5O12 with a spinel structure (space group Fd-3m). The as-synthesized powder showed a pure phase without detectable TiO2 or Li2TiO3 impurities according to XRD analysis.
JSON: ```JSON
[{"value": "Li4Ti5O12", "unit": "", "state": "pristine", "source_text": "The anode active material was identified as Li4Ti5O12 with a spinel structure"}]
```
"""

Crystal_System = """
Paragraph: The graphite anode exhibited a well-defined hexagonal crystal system, as determined by XRD analysis with characteristic (002) and (004) reflections. The lattice parameters refined to a = 2.464 Å and c = 6.711 Å.
JSON: ```JSON
[{"value": "hexagonal", "unit": "", "source_text": "The graphite anode exhibited a well-defined hexagonal crystal system"}]
```
"""

Space_Group = """
Paragraph: Rietveld refinement of synchrotron XRD data confirmed that the graphite anode crystallizes in the P63/mmc space group, characteristic of the ABAB Bernal stacking sequence. No evidence of rhombohedral stacking faults was detected.
JSON: ```JSON
[{"value": "P63/mmc", "unit": "", "source_text": "the graphite anode crystallizes in the P63/mmc space group"}]
```
"""

Lattice_Parameters = """
Paragraph: Rietveld refinement of the neutron diffraction pattern for the as-prepared Li4Ti5O12 spinel anode yielded lattice parameters a = b = 8.359 Å, c = 8.359 Å, α = β = γ = 90°, confirming the cubic Fd-3m symmetry. After lithiation to Li7Ti5O12, the lattice expanded to a = 8.367 Å.
JSON: ```JSON
[{"value": {"a": 8.359, "b": 8.359, "c": 8.359, "alpha": 90, "beta": 90, "gamma": 90}, "unit": "\u00c5 / \u00b0", "crystal_system": "cubic", "state": "pristine", "method": "neutron diffraction Rietveld refinement", "source_text": "Rietveld refinement of the neutron diffraction pattern for the as-prepared Li4Ti5O12 spinel anode yielded lattice parameters a = b = 8.359 \u00c5, c = 8.359 \u00c5, \u03b1 = \u03b2 = \u03b3 = 90\u00b0"},
{"value": {"a": 8.367, "b": 8.367, "c": 8.367, "alpha": 90, "beta": 90, "gamma": 90}, "unit": "\u00c5 / \u00b0", "crystal_system": "cubic", "state": "lithiated", "method": "neutron diffraction Rietveld refinement", "source_text": "After lithiation to Li7Ti5O12, the lattice expanded to a = 8.367 \u00c5"}]
```
"""

Crystallite_Size = """
Paragraph: The average crystallite size of the Si nanoparticles embedded in the carbon matrix was determined to be 12.5 nm using the Scherrer equation applied to the Si(111) diffraction peak at 2θ = 28.4°. TEM image analysis corroborated this with an average particle diameter of 14.3 nm.
JSON: ```JSON
[{"value": 12.5, "unit": "nm", "method": "Scherrer (XRD)", "source_text": "The average crystallite size of the Si nanoparticles was determined to be 12.5 nm using the Scherrer equation applied to the Si(111) diffraction peak"}]
```
"""

Interlayer_Spacing = """
Paragraph: XRD analysis of the expanded graphite anode showed a (002) interlayer spacing of 3.42 Å in the pristine state, which increased to 3.71 Å after full lithiation to LiC6, corresponding to a 8.5% expansion along the c-axis.
JSON: ```JSON
[{"value": 3.42, "unit": "\u00c5", "plane": "002", "state": "pristine", "source_text": "XRD analysis of the expanded graphite anode showed a (002) interlayer spacing of 3.42 \u00c5 in the pristine state"},
{"value": 3.71, "unit": "\u00c5", "plane": "002", "state": "lithiated", "source_text": "which increased to 3.71 \u00c5 after full lithiation to LiC6"}]
```
"""

Unit_Cell_Volume_Change = """
Paragraph: In-situ XRD during the first lithiation of the Si anode revealed a unit cell volume expansion from 160.2 Å³ (pristine c-Si, Fd-3m) to 312.4 Å³ (Li15Si4, I-43d), corresponding to a volume change of 95.0%. This drastic expansion is responsible for the severe pulverization observed during cycling.
JSON: ```JSON
[{"value": 95.0, "unit": "%", "from_state": "pristine c-Si", "to_state": "lithiated Li15Si4", "source_text": "a unit cell volume expansion from 160.2 \u00c5\u00b3 (pristine c-Si) to 312.4 \u00c5\u00b3 (Li15Si4), corresponding to a volume change of 95.0%"}]
```
"""

Band_Gap = """
Paragraph: DFT calculations using the HSE06 hybrid functional predicted an indirect band gap of 3.82 eV for the spinel Li4Ti5O12 anode material, consistent with its insulating nature and the experimentally observed flat operating voltage plateau at 1.55 V vs. Li/Li+. The PBE functional underestimated the gap at 2.14 eV.
JSON: ```JSON
[{"value": 3.82, "unit": "eV", "type": "indirect", "method": "HSE06", "source_text": "DFT calculations using the HSE06 hybrid functional predicted an indirect band gap of 3.82 eV for the spinel Li4Ti5O12"},
{"value": 2.14, "unit": "eV", "type": "indirect", "method": "PBE", "source_text": "The PBE functional underestimated the gap at 2.14 eV"}]
```
"""

Li_Ion_Migration_Barrier = """
Paragraph: Climbing-image nudged elastic band (CI-NEB) calculations revealed a Li-ion migration barrier of 0.28 eV for diffusion along the ab-plane in graphite (intra-layer pathway), significantly lower than the 0.85 eV barrier for inter-layer migration along the c-axis.
JSON: ```JSON
[{"value": 0.28, "unit": "eV", "path": "intra-layer (ab-plane)", "method": "CI-NEB", "source_text": "CI-NEB calculations revealed a Li-ion migration barrier of 0.28 eV for diffusion along the ab-plane in graphite (intra-layer pathway)"},
{"value": 0.85, "unit": "eV", "path": "inter-layer (c-axis)", "method": "CI-NEB", "source_text": "the 0.85 eV barrier for inter-layer migration along the c-axis"}]
```
"""

Theoretical_Specific_Capacity = """
Paragraph: The theoretical specific capacity of the silicon anode was calculated as 3579 mAh/g based on the formation of Li15Si4 at room temperature (n = 3.75 electrons per Si atom), using Faraday's law: Q = nF/(3.6 × M). For the fully lithiated Li22Si5 phase accessible at elevated temperature, the theoretical capacity reaches 4200 mAh/g (n = 4.4).
JSON: ```JSON
[{"value": 3579, "unit": "mAh/g", "n_electrons": 3.75, "redox_reaction": "Si + 3.75Li -> Li3.75Si (Li15Si4)", "source_text": "The theoretical specific capacity of the silicon anode was calculated as 3579 mAh/g based on the formation of Li15Si4 at room temperature (n = 3.75 electrons per Si atom)"}]
```
"""

LiF_Content_in_SEI_XPS = """
Paragraph: XPS depth profiling of the graphite anode after 50 cycles in 1M LiPF6 EC/DMC electrolyte revealed a LiF content of 12.8 at% in the SEI outer layer (F 1s spectrum), increasing to 18.3 at% in the inner SEI region closer to the electrode surface. After 200 cycles, the LiF content further rose to 22.1 at% in the inner layer.
JSON: ```JSON
[{"value": 12.8, "unit": "at%", "state": "after 50 cycles; outer SEI", "spectrum": "F 1s", "source_text": "XPS depth profiling of the graphite anode after 50 cycles revealed a LiF content of 12.8 at% in the SEI outer layer"},
{"value": 18.3, "unit": "at%", "state": "after 50 cycles; inner SEI", "spectrum": "F 1s", "source_text": "increasing to 18.3 at% in the inner SEI region"},
{"value": 22.1, "unit": "at%", "state": "after 200 cycles; inner SEI", "spectrum": "F 1s", "source_text": "the LiF content further rose to 22.1 at% in the inner layer"}]
```
"""

SEI_Chemical_Composition_XPS = """
Paragraph: Ex-situ XPS analysis of the cycled Si/C composite anode identified the following SEI species on the electrode surface after formation cycles: LiF, Li2CO3, ROCO2Li, Li2O, and LixPOyFz. The presence of LixPOyFz was attributed to the decomposition of the LiPF6 salt, while ROCO2Li originated from EC solvent reduction.
JSON: ```JSON
[{"value": ["LiF", "Li2CO3", "ROCO2Li", "Li2O", "LixPOyFz"], "unit": "", "state": "after formation cycles", "source_text": "Ex-situ XPS analysis of the cycled Si/C composite anode identified the following SEI species: LiF, Li2CO3, ROCO2Li, Li2O, and LixPOyFz"}]
```
"""

Exchange_Current_Density = """
Paragraph: Tafel analysis of the graphite anode in 1M LiPF6 EC/EMC (3:7) electrolyte yielded an exchange current density i0 of 0.42 mA/cm² at 25 °C, indicative of facile charge transfer kinetics at the graphite/electrolyte interface. At elevated temperature of 45 °C, i0 increased to 0.78 mA/cm².
JSON: ```JSON
[{"value": 0.42, "unit": "mA/cm2", "method": "Tafel analysis", "temperature_C": 25, "source_text": "Tafel analysis of the graphite anode yielded an exchange current density i0 of 0.42 mA/cm\u00b2 at 25 \u00b0C"},
{"value": 0.78, "unit": "mA/cm2", "method": "Tafel analysis", "temperature_C": 45, "source_text": "At elevated temperature of 45 \u00b0C, i0 increased to 0.78 mA/cm\u00b2"}]
```
"""

# ==================== Agent 2: 电化学性能 (26 标签) ====================

Initial_Coulombic_Efficiency = """
Paragraph: The Si/C composite anode delivered a first lithiation capacity of 2450 mAh/g and a first delithiation capacity of 1985 mAh/g at 0.1C, resulting in an initial Coulombic efficiency of 81.0%. The irreversible capacity loss of 465 mAh/g was primarily attributed to SEI formation on the high-surface-area carbon matrix.
JSON: ```JSON
[{"value": 81.0, "unit": "%", "condition_id": "C001", "source_text": "resulting in an initial Coulombic efficiency of 81.0%"}]
```
"""

First_Lithiation_Capacity = """
Paragraph: The first lithiation capacity of the nanostructured Si anode was measured as 3120 mAh/g at a current density of 200 mA/g in a half-cell configuration with Li metal as the counter electrode, within the voltage window of 0.01-1.0 V vs. Li/Li+.
JSON: ```JSON
[{"value": 3120, "unit": "mAh/g", "condition_id": "C001", "source_text": "The first lithiation capacity of the nanostructured Si anode was measured as 3120 mAh/g at a current density of 200 mA/g"}]
```
"""

Reversible_Capacity_First_Cycle = """
Paragraph: The SiO@C@Al2O3 composite delivered a reversible specific capacity of 1433.7 mAh/g with an initial coulombic efficiency of 77.81% at 0.1C in a half-cell configuration.
JSON: ```JSON
[{"value": 1433.7, "unit": "mAh/g", "condition_id": "C001", "source_text": "The SiO@C@Al2O3 composite delivered a reversible specific capacity of 1433.7 mAh/g"}]
```
"""

Pseudocapacitive_Contribution_Ratio = """
Paragraph: CV measurements of the mesoporous hard carbon anode at scan rates from 0.1 to 5.0 mV/s revealed a pseudocapacitive contribution of 62.3% at 1.0 mV/s, as determined by the power-law relationship i = aν^b. The capacitive contribution increased to 78.5% at 5.0 mV/s.
JSON: ```JSON
[{"value": 62.3, "unit": "%", "condition_id": "C001", "scan_rate_mV_s": 1.0, "source_text": "a pseudocapacitive contribution of 62.3% at 1.0 mV/s"},
{"value": 78.5, "unit": "%", "condition_id": "C001", "scan_rate_mV_s": 5.0, "source_text": "The capacitive contribution increased to 78.5% at 5.0 mV/s"}]
```
"""

Rate_Capability_at_Given_C_rate = """
Paragraph: The graphite anode exhibited discharge capacities of 360 and 210 mAh/g at rates of 0.1C and 5C respectively, when tested in a half-cell between 0.01-1.5 V vs. Li/Li+ at 25 °C.
JSON: ```JSON
[{"value": 360, "unit": "mAh/g", "condition_id": "C001", "voltage_range": "0.01-1.5 V vs. Li/Li+", "source_text": "The graphite anode exhibited discharge capacities of 360 mAh/g at 0.1C"},
{"value": 210, "unit": "mAh/g", "condition_id": "C001", "voltage_range": "0.01-1.5 V vs. Li/Li+", "source_text": "and 210 mAh/g at 5C"}]
```
"""

Energy_Density_Full_Cell = """
Paragraph: The Si/graphite || NCM811 full cell delivered a gravimetric energy density of 385 Wh/kg based on the total mass of both electrodes at the cell level, operating between 2.8-4.2 V. This exceeds the 260 Wh/kg energy density of the conventional graphite || NCM811 reference cell by 48%.
JSON: ```JSON
[{"value": 385, "unit": "Wh/kg", "condition_id": "C001", "basis": "total electrode mass (cell level)", "source_text": "The Si/graphite || NCM811 full cell delivered a gravimetric energy density of 385 Wh/kg based on the total mass of both electrodes"},
{"value": 260, "unit": "Wh/kg", "condition_id": "C002", "basis": "total electrode mass (cell level)", "source_text": "the 260 Wh/kg energy density of the conventional graphite || NCM811 reference cell"}]
```
"""

Critical_Current_Density_Dendrite = """
Paragraph: Galvanostatic plating/stripping tests on the 3D porous Cu current collector revealed a critical current density for lithium dendrite formation of 3.5 mA/cm² in 1M LiPF6 EC/DMC (1:1) electrolyte at 25 °C, compared to only 0.8 mA/cm² for the planar Cu foil under identical conditions.
JSON: ```JSON
[{"value": 3.5, "unit": "mA/cm2", "condition_id": "C001", "source_text": "a critical current density for lithium dendrite formation of 3.5 mA/cm\u00b2 in 1M LiPF6 EC/DMC (1:1) electrolyte at 25 \u00b0C"},
{"value": 0.8, "unit": "mA/cm2", "condition_id": "C002", "source_text": "only 0.8 mA/cm\u00b2 for the planar Cu foil under identical conditions"}]
```
"""

Volumetric_Capacity = """
Paragraph: The densified Si/C composite anode with a compaction density of 1.45 g/cm³ achieved a volumetric capacity of 1850 mAh/cm³ at 0.1C, significantly higher than the 550 mAh/cm³ of the conventional graphite anode.
JSON: ```JSON
[{"value": 1850, "unit": "mAh/cm3", "condition_id": "C001", "source_text": "The densified Si/C composite anode achieved a volumetric capacity of 1850 mAh/cm\u00b3 at 0.1C"}]
```
"""

Areal_Capacity = """
Paragraph: The thick graphite electrode with a mass loading of 8.5 mg/cm² delivered an areal capacity of 3.1 mAh/cm² at 0.1C, meeting the practical requirement of >3 mAh/cm² for high-energy-density lithium-ion batteries.
JSON: ```JSON
[{"value": 3.1, "unit": "mAh/cm2", "condition_id": "C001", "source_text": "The thick graphite electrode delivered an areal capacity of 3.1 mAh/cm\u00b2 at 0.1C"}]
```
"""

Average_Operating_Voltage = """
Paragraph: The Li4Ti5O12 anode exhibited an average discharge voltage of 1.55 V (vs. Li/Li+), calculated by integrating the discharge energy over capacity at 0.2C between 1.0-2.5 V. The flat voltage plateau is characteristic of the two-phase Li4Ti5O12/Li7Ti5O12 redox reaction.
JSON: ```JSON
[{"value": 1.55, "unit": "V", "condition_id": "C001", "reference": "Li/Li+", "source_text": "The Li4Ti5O12 anode exhibited an average discharge voltage of 1.55 V (vs. Li/Li+), calculated by integrating the discharge energy over capacity"}]
```
"""

Cycle_Life_80_Retention = """
Paragraph: The SiOx/graphite composite anode demonstrated outstanding cycling stability, retaining 80% of its initial capacity after 850 cycles at 1C in a half-cell configuration with 1M LiPF6 FEC/EC/EMC electrolyte.
JSON: ```JSON
[{"value": 850, "unit": "cycles", "condition_id": "C001", "retention_cutoff": 80, "source_text": "retaining 80% of its initial capacity after 850 cycles at 1C"}]
```
"""

Rate_Recovery = """
Paragraph: After cycling at high rates up to 20C, the nanoporous hard carbon anode recovered 98.5% of its initial 0.2C capacity when the rate was returned to 0.2C, demonstrating excellent structural reversibility. The rate recovery test was performed by cycling 5 times each at 0.2C, 1C, 5C, 10C, 20C, and finally back to 0.2C.
JSON: ```JSON
[{"value": 98.5, "unit": "%", "condition_id": "C001", "high_rate": "20C", "low_rate": "0.2C", "source_text": "the nanoporous hard carbon anode recovered 98.5% of its initial 0.2C capacity when the rate was returned to 0.2C"}]
```
"""

Symmetric_Cell_Stability = """
Paragraph: The Li||Li symmetric cell with the Li3N-rich artificial SEI exhibited stable galvanostatic cycling for over 1200 hours at a current density of 1.0 mA/cm² with a fixed areal capacity of 1.0 mAh/cm², maintaining a low overpotential of 18 mV throughout the test.
JSON: ```JSON
[{"value": {"time_h": 1200, "overpotential_mV": 18}, "unit": "h/mV", "condition_id": "C001", "current_density_mA_cm2": 1.0, "areal_capacity_mAh_cm2": 1.0, "source_text": "The Li||Li symmetric cell exhibited stable galvanostatic cycling for over 1200 hours at a current density of 1.0 mA/cm\u00b2 with a fixed areal capacity of 1.0 mAh/cm\u00b2, maintaining a low overpotential of 18 mV"}]
```
"""

Average_Coulombic_Efficiency_Stable = """
Paragraph: The carbon-coated Si anode achieved an average Coulombic efficiency of 99.85% during the stable cycling period from cycle 5 to cycle 300 at 0.5C, after the initial SEI formation cycles. The CE exceeded 99.9% beyond cycle 50.
JSON: ```JSON
[{"value": 99.85, "unit": "%", "condition_id": "C001", "cycle_range": [5, 300], "source_text": "an average Coulombic efficiency of 99.85% during the stable cycling period from cycle 5 to cycle 300"}]
```
"""

Capacity_Retention_at_Nth_Cycle = """
Paragraph: The SnO2/graphene composite anode retained 88.5% of its initial discharge capacity after 500 cycles at 1C, corresponding to a capacity fade rate of only 0.023% per cycle.
JSON: ```JSON
[{"value": 88.5, "unit": "%", "condition_id": "C001", "cycle_number": 500, "source_text": "The SnO2/graphene composite anode retained 88.5% of its initial discharge capacity after 500 cycles"}]
```
"""

Open_Circuit_Voltage = """
Paragraph: The open circuit voltage of the lithiated graphite anode was measured as 0.12 V at 50% SOC after a rest time of 4 hours, approaching the equilibrium potential of the LiC12/LiC6 two-phase region. The OCV was recorded in a three-electrode cell with Li metal as both counter and reference electrodes at 25 °C.
JSON: ```JSON
[{"value": 0.12, "unit": "V", "condition_id": "C001", "soc_percent": 50, "rest_time_h": 4, "source_text": "The open circuit voltage of the lithiated graphite anode was measured as 0.12 V at 50% SOC after a rest time of 4 hours"}]
```
"""

Surface_Controlled_Contribution = """
Paragraph: Kinetics analysis of the Nb2O5/graphene composite anode revealed a surface-controlled capacitive contribution of 72.5% at a scan rate of 2.0 mV/s, based on the Dunn method deconvolution of the CV curves.
JSON: ```JSON
[{"value": 72.5, "unit": "%", "condition_id": "C001", "scan_rate_mV_s": 2.0, "source_text": "a surface-controlled capacitive contribution of 72.5% at a scan rate of 2.0 mV/s"}]
```
"""

Irreversible_Capacity_Loss_First = """
Paragraph: The pristine Si anode exhibited a first-cycle irreversible capacity loss of 750 mAh/g, corresponding to an ICE of only 71%. The large irreversible loss was primarily due to extensive electrolyte decomposition and SEI formation on the native SiOx surface layer.
JSON: ```JSON
[{"value": 750, "unit": "mAh/g", "condition_id": "C001", "source_text": "The pristine Si anode exhibited a first-cycle irreversible capacity loss of 750 mAh/g"}]
```
"""

SEI_Ionic_Conductivity = """
Paragraph: Electrochemical impedance spectroscopy (EIS) measurements on the graphite anode after formation cycles yielded an SEI ionic conductivity of 2.8 × 10⁻⁸ S/cm at 25 °C, determined by fitting the high-frequency semicircle to an RC equivalent circuit. The SEI conductivity increased to 5.1 × 10⁻⁸ S/cm at 45 °C.
JSON: ```JSON
[{"value": 2.8e-8, "unit": "S/cm", "condition_id": "C001", "temperature_C": 25, "method": "EIS", "source_text": "EIS measurements on the graphite anode after formation cycles yielded an SEI ionic conductivity of 2.8 \u00d7 10\u207b\u2078 S/cm at 25 \u00b0C"}]
```
"""

SEI_Resistance = """
Paragraph: EIS analysis of the Si/C anode at 50% SOC after 100 cycles showed an SEI resistance (R_SEI) of 12.3 Ω from the high-frequency semicircle, while the charge transfer semicircle gave Rct of 28.5 Ω. The growth of interfacial resistance was attributed to continuous SEI thickening.
JSON: ```JSON
[{"value": 12.3, "unit": "\u03a9", "condition_id": "C001", "cycle_number": 100, "soc_percent": 50, "source_text": "EIS analysis of the Si/C anode at 50% SOC after 100 cycles showed an SEI resistance (R_SEI) of 12.3 \u03a9 from the high-frequency semicircle"}]
```
"""

Charge_Transfer_Resistance = """
Paragraph: EIS analysis of the Si/C anode at 50% SOC after 100 cycles showed a charge transfer resistance (Rct) of 28.5 Ω, significantly increased from the initial value of 12.3 Ω after formation.
JSON: ```JSON
[{"value": 28.5, "unit": "\u03a9", "condition_id": "C001", "cycle_number": 100, "soc_percent": 50, "source_text": "EIS analysis of the Si/C anode at 50% SOC after 100 cycles showed a charge transfer resistance (Rct) of 28.5 \u03a9"},
{"value": 12.3, "unit": "\u03a9", "condition_id": "C001", "cycle_number": 1, "soc_percent": 50, "source_text": "the initial value of 12.3 \u03a9 after formation"}]
```
"""

Chemical_Diffusion_Coefficient_GITT = """
Paragraph: GITT measurements on the graphite anode during the first lithiation at 25 °C yielded a lithium-ion chemical diffusion coefficient of 1.2 × 10⁻¹⁰ cm²/s at 0.2 V (dilute stage I), decreasing to a minimum of 3.2 × 10⁻¹² cm²/s near the LiC12/LiC6 phase boundary at 0.1 V.
JSON: ```JSON
[{"value": 1.2e-10, "unit": "cm2/s", "condition_id": "C001", "voltage_V": 0.2, "temperature_C": 25, "source_text": "GITT measurements on the graphite anode yielded a lithium-ion chemical diffusion coefficient of 1.2 \u00d7 10\u207b\u00b9\u2070 cm\u00b2/s at 0.2 V"},
{"value": 3.2e-12, "unit": "cm2/s", "condition_id": "C001", "voltage_V": 0.1, "temperature_C": 25, "source_text": "a minimum of 3.2 \u00d7 10\u207b\u00b9\u00b2 cm\u00b2/s near the LiC12/LiC6 phase boundary at 0.1 V"}]
```
"""

Li_Dendrite_Nucleation_Overpotential = """
Paragraph: Galvanostatic Li plating on the Ag-modified Cu current collector showed a nucleation overpotential of 18 mV at a current density of 0.5 mA/cm², compared to 42 mV for bare Cu. The reduced nucleation barrier was attributed to the formation of a Li-Ag solid solution interlayer.
JSON: ```JSON
[{"value": 18, "unit": "mV", "condition_id": "C001", "current_density_mA_cm2": 0.5, "substrate": "Ag-modified Cu", "source_text": "a nucleation overpotential of 18 mV at a current density of 0.5 mA/cm\u00b2"},
{"value": 42, "unit": "mV", "condition_id": "C002", "current_density_mA_cm2": 0.5, "substrate": "bare Cu", "source_text": "compared to 42 mV for bare Cu"}]
```
"""

Li_Dendrite_Growth_Rate = """
Paragraph: In-situ optical microscopy observations revealed a lithium dendrite growth rate of 0.85 μm/min on the bare Li metal surface at a current density of 2.0 mA/cm² in 1M LiTFSI DOL/DME electrolyte. In contrast, the LiF-rich artificial SEI suppressed dendritic growth entirely.
JSON: ```JSON
[{"value": 0.85, "unit": "\u03bcm/min", "condition_id": "C001", "current_density_mA_cm2": 2.0, "method": "in-situ optical microscopy", "source_text": "a lithium dendrite growth rate of 0.85 \u03bcm/min on the bare Li metal surface at a current density of 2.0 mA/cm\u00b2"}]
```
"""

Activation_Energy_SEI_Transport = """
Paragraph: Temperature-dependent EIS measurements from -10 °C to 50 °C on the graphite anode after formation cycles yielded an activation energy of 48.2 kJ/mol for Li+ transport through the SEI layer, derived from the Arrhenius relationship σT = A·exp(-Ea/RT).
JSON: ```JSON
[{"value": 48.2, "unit": "kJ/mol", "condition_id": "C001", "temperature_range_C": "-10 to 50 °C", "method": "temperature-dependent EIS", "source_text": "yielded an activation energy of 48.2 kJ/mol for Li+ transport through the SEI layer, derived from the Arrhenius relationship"}]
```
"""

Activation_Energy_Desolvation = """
Paragraph: Variable-temperature EIS and DFT-MD simulations revealed a Li+ desolvation activation energy of 55.8 kJ/mol at the graphite/EC-DMC electrolyte interface, representing the dominant kinetic barrier for charge transfer at low temperatures. The addition of 5% FEC reduced the desolvation barrier to 48.3 kJ/mol by modifying the Li+ solvation sheath structure.
JSON: ```JSON
[{"value": 55.8, "unit": "kJ/mol", "condition_id": "C001", "temperature_range_C": "-20 to 60 °C", "method": "variable-temperature EIS + DFT-MD", "source_text": "revealed a Li+ desolvation activation energy of 55.8 kJ/mol at the graphite/EC-DMC electrolyte interface"},
{"value": 48.3, "unit": "kJ/mol", "condition_id": "C002", "temperature_range_C": "-20 to 60 °C", "method": "variable-temperature EIS + DFT-MD", "source_text": "The addition of 5% FEC reduced the desolvation barrier to 48.3 kJ/mol"}]
```
"""

Li_Nucleation_Overpotential = """
Paragraph: Chronopotentiometry measurements during the initial Li deposition on the lithiophilic ZnO-coated Cu substrate showed a nucleation overpotential of 8 mV at 0.2 mA/cm², compared to 35 mV on bare Cu. The dramatically reduced nucleation barrier was ascribed to the in-situ formation of a LiZn alloy layer.
JSON: ```JSON
[{"value": 8, "unit": "mV", "condition_id": "C001", "current_density_mA_cm2": 0.2, "source_text": "a nucleation overpotential of 8 mV at 0.2 mA/cm\u00b2"},
{"value": 35, "unit": "mV", "condition_id": "C002", "current_density_mA_cm2": 0.2, "source_text": "compared to 35 mV on bare Cu"}]
```
"""

Plateau_Overpotential = """
Paragraph: The Li||Cu half-cell with the FEC-containing electrolyte exhibited a stable Li plating/stripping plateau overpotential of 25 mV at a current density of 1.0 mA/cm² with a capacity of 1.0 mAh/cm², maintained for over 300 cycles.
JSON: ```JSON
[{"value": 25, "unit": "mV", "condition_id": "C001", "current_density_mA_cm2": 1.0, "capacity_mAh_cm2": 1.0, "source_text": "a stable Li plating/stripping plateau overpotential of 25 mV at a current density of 1.0 mA/cm\u00b2 with a capacity of 1.0 mAh/cm\u00b2"}]
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
