# -*- coding: utf-8 -*-
"""
Cathode Material Extraction Rules

Each tag structure: Definition + Extraction requirements (including unit standardization, exclusion conditions, and computational notes).
Component name/type is handled by the pipeline's material_id and is not duplicated within individual tags.
"""

# ==================== Agent 1: Intrinsic Material Properties ====================

Lattice_Parameters = """
Definition: The three lattice constants (a, b, c, in Å) and three interaxial angles (α, β, γ, in degrees) of a unit cell. This is the most fundamental geometric description of a crystal structure, used to calculate unit cell volume, lattice strain during delithiation/lithiation, and anisotropic expansion.
Extraction requirements:
- Output as an object containing a, b, c, alpha, beta, gamma, all as floats.
- If only partial parameters are given (e.g., only a for a cubic system), set missing fields to 0.0 and note.
- Units: lattice constants uniformly in Å (1 nm = 10 Å), angles uniformly in degrees.
- Must specify the crystal system to correctly interpret the lattice parameters (e.g., cubic: a = b = c, α = β = γ = 90°).
- State: pristine, lithiated, delithiated, charged, discharged.
- Method: XRD Rietveld refinement, single-crystal XRD, neutron diffraction.
- If both calculated and experimental values are reported, extract as two separate objects.
"""

Crystal_Space_Group = """
Definition: The space group symbol in Hermann–Mauguin notation. Common space groups for cathode materials include R-3m (layered oxides), Fd-3m (spinel), and Pnma (olivine). The space group determines the topology of lithium ion diffusion channels and the phase transition pathways.
Extraction requirements:
- Preserve the standard representation; ASCII forms (e.g., R-3m, Fd-3m, Pnma) are acceptable.
- If multiple phases are reported (e.g., O3 and O2 structures), record each phase as a separate object.
- Exclude amorphous materials.
"""

Lithium_Ion_Diffusion_Activation_Energy = """
Definition: The minimum energy required for Li⁺ hopping within the cathode crystal lattice (diffusion activation energy), in eV. It can be obtained from DFT calculations (NEB) or from variable-temperature electrochemical methods (GITT/EIS) via Arrhenius fitting. A lower activation energy indicates faster Li⁺ diffusion and better rate capability.
Extraction requirements:
- Unit in eV. If given in kJ/mol, divide by 96.485 for conversion.
- Must specify the method used (DFT / EIS / GITT).
- For DFT calculations, specify the functional (e.g., PBE, HSE06) and the migration pathway (e.g., ab-plane, along c-axis).
- For experimental values, specify the temperature range and state of charge (SOC).
"""

Li_Ion_Migration_Barrier = """
Definition: The local energy barrier for Li⁺ migration along a specific pathway, in eV. Although similar in meaning to the diffusion activation energy, this term emphasizes a specific migration path (e.g., octahedron → tetrahedron → octahedron) and the corresponding energetic impediment.
Extraction requirements:
- Unit in eV. If given in kJ/mol, divide by 96.485 for conversion.
- Must specify the migration pathway (e.g., oct→tet→oct, intra-layer, inter-layer).
- Restricted to DFT-calculated values (NEB/CI-NEB).
"""

Electronic_Band_Gap = """
Definition: The electronic band gap, i.e., the energy difference between the conduction band minimum (CBM) and the valence band maximum (VBM), in eV. It governs the intrinsic electronic conductivity of the cathode material: zero or small gap corresponds to metallic/semi-metallic behavior (e.g., NMC), while a large gap corresponds to semiconducting/insulating behavior (e.g., LFP, ~3.8 eV).
Extraction requirements:
- Unit in eV.
- Distinguish between experimental values (e.g., UV-vis DRS, UPS) and calculated values (via the method field).
- Distinguish between direct and indirect band gaps (via the type field).
- For calculated values, specify the functional (e.g., PBE, HSE06).
"""

Theoretical_Specific_Capacity = """
Definition: The theoretical specific capacity, in mAh/g. Based on Faraday's law: Q = (n × F) / (3.6 × M), where n is the number of Li⁺ ions extracted per formula unit (number of electrons transferred), F = 96485 C/mol, and M is the molar mass (g/mol).
Extraction requirements:
- Unit in mAh/g.
- [Calculated tag] Recommended to be computed automatically by the pipeline from the chemical formula and the specified extent of delithiation, rather than extracted from the literature.
- If the literature provides explicit n and the corresponding reaction, it can be extracted; the basis for delithiation (e.g., n = 0.5 for LiCoO₂) must be noted.
"""

Formation_Energy = """
Definition: The enthalpy change when one formula unit of a compound is formed from its constituent elements in their standard states, in eV/f.u. or kJ/mol. A more negative value indicates greater thermodynamic stability.
Extraction requirements:
- Unit in eV/f.u. If given in kJ/mol, divide by 96.485 for conversion (1 eV/f.u. ≈ 96.485 kJ/mol).
- Specify the calculation method (e.g., DFT, Materials Project) and the reference states (elemental standard states).
- Prefer values from public databases (Materials Project, OQMD); retain experimental values if reported in the literature.
"""

Volume_Change_Ratio = """
Definition: The percentage change in unit cell volume (%). For cathodes, delithiation typically results in volume contraction (positive values), while lithiation leads to expansion (negative values). Formula: (V_delithiated − V_lithiated) / V_lithiated × 100%.
Extraction requirements:
- Unit in %.
- Must explicitly state from_state (e.g., fully lithiated) and to_state (e.g., fully delithiated).
- Do not extract if the literature only reports a/c-axis changes without calculating the volume change.
"""

a_c_axis_expansion = """
Definition: The anisotropic lattice parameter changes in layered cathode materials during delithiation. Typically, the c-axis elongates (Δc > 0) and the a-axis contracts (Δa < 0), with the reverse occurring upon lithiation. Δa and Δc are in Å; ΔV is in Å³ or %.
Extraction requirements:
- Output as an object containing delta_a (Å), delta_c (Å), delta_V (Å³ or %), and the corresponding state pair.
- If only percentage changes are given, extract the numerical values and specify the unit as %.
"""

Oxygen_Vacancy_Concentration = """
Definition: The concentration of oxygen vacancies, expressed as the number of missing oxygen atoms per unit volume or per formula unit. Can be denoted as x in LiCoO₂₋ₓ. Oxygen vacancies enhance electronic conductivity but excessive concentrations lead to structural degradation.
Extraction requirements:
- Unit: mole fraction (e.g., 0.02) or cm⁻³.
- Must specify the measurement method (e.g., TGA, EELS, neutron diffraction) or the calculation conditions (DFT).
- Exclude speculative descriptions that state existence without quantification.
"""

Oxygen_Vacancy_Formation_Energy = """
Definition: The energy required to remove one neutral oxygen atom from a perfect crystal to create a vacancy, in eV.
Extraction requirements:
- Unit in eV.
- Must specify the calculation parameters (DFT functional, whether chemical potential corrections are applied).
- If values at multiple vacancy concentrations are given, extract the value closest to the stoichiometric composition.
"""

Transition_Metal_Migration_Energy_Barrier = """
Definition: The energy barrier for a transition metal ion (Ni, Co, Mn, Fe, etc.) to migrate from its original lattice site to an adjacent site, in eV. A high barrier suppresses cation mixing and phase transitions.
Extraction requirements:
- Unit in eV.
- Specify the migration pathway (e.g., TM layer → Li layer) and the calculation method (DFT + NEB).
- Distinguish between different transition metal species (e.g., Ni migration barrier vs. Mn migration barrier).
"""

Interlayer_Spacing_of_TM_Layers = """
Definition: The perpendicular distance between adjacent transition metal layers, in Å. In layered cathodes, it is typically approximated by the (003) interplanar spacing. A larger interlayer spacing facilitates faster Li⁺ diffusion.
Extraction requirements:
- Unit in Å (1 nm = 10 Å).
- Must specify the corresponding crystallographic plane (e.g., "003") and material state (pristine / delithiated).
- Do not extract if the plane is not specified.
"""

Density_of_States_at_Fermi_Level = """
Definition: The electronic density of states at the Fermi level, N(E_F), in units of states·eV⁻¹·cell⁻¹ or states·eV⁻¹·atom⁻¹. A non-zero value indicates metallic electronic conductivity.
Extraction requirements:
- Numerical value and unit.
- Must specify the calculation method (DFT, functional) and the normalization convention (per cell / per atom).
"""

Bader_Charge = """
Definition: The Bader charge based on topological analysis of the electron density, in units of e (electron charge). It reflects the charge transfer between atoms and the ionicity of chemical bonds.
Extraction requirements:
- Output as an object with keys representing element symbols (e.g., "Ni", "O") and values as floats (unit: e).
- Must specify the computational software (e.g., VASP) and calculation conditions.
"""

Chemical_Composition_Mole_Fractions = """
Definition: The mole fractions of each metallic element (Li, Ni, Co, Mn, Fe, Al, etc.) in the cathode material. Oxygen is excluded from the summation. All fractions sum to 1.
Extraction requirements:
- Output as a dictionary, e.g., {"Li": 1.0, "Ni": 0.33, "Co": 0.33, "Mn": 0.33}.
- If the literature provides a chemical formula such as LiNi₀.₃₃Co₀.₃₃Mn₀.₃₃O₂, extract the mole fractions and note the Li content.
- Exclude dopant elements below 0.5 at% (unless the study specifically addresses doping effects).
"""

Element_Valence_State = """
Definition: The oxidation state of transition metal elements in the cathode material (e.g., Ni²⁺, Ni³⁺, Ni⁴⁺, Co³⁺, Mn⁴⁺). The valence state determines the charge compensation mechanism and the average voltage.
Extraction requirements:
- Output as a dictionary with element symbols as keys and valence states (integers or signed integers, e.g., +2, +3, +4) as values.
- Must specify the state (as-synthesized, charged, discharged) and measurement method (XPS, XANES).
- If multiple valence states coexist (e.g., Ni²⁺/Ni³⁺ mixture), report the average valence state or the ratio.
"""

Jahn_Teller_Active_Ion_Content = """
Definition: The mole fraction (0–1) of Jahn–Teller active ions (e.g., Mn³⁺, Ni³⁺, Cu²⁺) among the total transition metals.
Extraction requirements:
- Unit: mole fraction.
- [Calculated tag] Automatically computed from element valence states and mole fractions: JT_content = Σ(x_i) for i in JT_active_ions.
- If the literature directly provides the JT ion ratio, it can also be extracted with the source noted.
"""

devtE = """
Definition: The mean absolute deviation of total valence electron count, dimensionless. It quantifies the degree of dispersion of valence electron numbers among the constituent elements.
Extraction requirements:
- [Calculated tag] Not extracted directly from the literature; computed automatically by the pipeline from the chemical formula and valence states.
- Calculation method:
  1. Obtain the total valence electron count for each metal element (regardless of valence state; e.g., Ni = 10, Co = 9, Mn = 7, Li = 1, Mg = 2, Al = 3, Ti = 4).
  2. Compute the weighted average μ = Σ(x_i × VE_i).
  3. devtE = Σ(x_i × |VE_i − μ|).
- If the literature directly provides devtE without a detailed chemical formula, it may be extracted as a fallback, with the source noted.
"""

VEd = """
Definition: The weighted average d-orbital electron count, dimensionless. It influences the crystal field stabilization energy and redox behavior.
Extraction requirements:
- [Calculated tag] Automatically computed by the pipeline from the transition metal species, valence states, and mole fractions.
- Calculation method: VEd = Σ(x_i × d_electron_count_i), where d_electron_count depends on the valence state (e.g., Ni²⁺ = 8, Ni³⁺ = 7, Co³⁺ = 6, Mn⁴⁺ = 3, Fe³⁺ = 5, Ti⁴⁺ = 0).
- Default valence state assumptions follow global rules; if experimental valence states are reported, use those preferentially.
"""

Average_Electron_Affinity = """
Definition: The average electron affinity, in eV. The mole-fraction-weighted average of the electron affinities of each metal element.
Extraction requirements:
- Unit in eV.
- [Calculated tag] Automatically computed by the pipeline from an elemental database (e.g., Mendeleev): EA_avg = Σ(x_i × EA_i).
"""

Average_Deviation_of_Ionic_Radius = """
Definition: The average deviation of ionic radii (typically the mean absolute deviation), in pm. It reflects the degree of ionic size mismatch.
Extraction requirements:
- Unit in pm.
- [Calculated tag] Automatically computed by the pipeline:
  1. Look up Shannon radii r_i (pm) based on the valence state.
  2. Compute the weighted average μ = Σ(x_i × r_i).
  3. Mean absolute deviation = Σ(x_i × |r_i − μ|).
"""

Average_Ionization_Energy = """
Definition: The average ionization energy, in eV. The weighted average of the first ionization energies of each metal element.
Extraction requirements:
- Unit in eV.
- [Calculated tag] Automatically computed by the pipeline from an elemental database: IE_avg = Σ(x_i × IE_i).
"""

Configurational_Entropy = """
Definition: The configurational entropy, in J/(mol·K). Formula: S_config = −R Σ x_i ln x_i, where R = 8.314 J/(mol·K). Used for high-entropy cathodes.
Extraction requirements:
- Unit in J/(mol·K).
- [Calculated tag] Automatically computed by the pipeline from the transition metal site mole fractions.
- If the literature specifies calculations for different crystallographic sites (e.g., Li site, TM site), the site should be clearly indicated.
"""

Valence_Electron_Count = """
Definition: The total valence electron count of an element (e.g., Ni = 10, Co = 9, Mn = 7, Li = 1) or its weighted average.
Extraction requirements:
- Output as a dictionary (element: valence electron count) or a weighted average (dimensionless).
- [Calculated tag] Can be auto-generated; if the literature directly provides the average value, it may also be extracted.
"""

d_Electron_Configuration_Type = """
Definition: The d-electron configuration classification, e.g., d⁰ (Ti⁴⁺), d¹⁰ (Cu⁺, Zn²⁺), s-block (Li⁺, Mg²⁺).
Extraction requirements:
- Output as a string.
- [Calculated tag] Automatically determined from the element and its valence state.
"""

Li_Ni_mixing_ratio = """
Definition: The Li/Ni cation mixing ratio (the atomic percentage of Ni occupying Li sites), reflecting the degree of cation disorder.
Extraction requirements:
- Unit in at% or as a fraction (if given as a fraction, convert to percentage).
- Must specify the determination method (XRD Rietveld, neutron diffraction) and the state (pristine / cycled).
- Do not extract intensity ratios I(003)/I(104) as a direct measure of cation mixing.
"""

Metal_Oxygen_Bond_Energy = """
Definition: The metal–oxygen bond energy (average bond dissociation energy), in eV. Reflects the strength of the M–O bond.
Extraction requirements:
- Unit in eV. If given in kJ/mol, divide by 96.485 for conversion.
- For calculated values, specify the DFT functional; for experimental values, specify the method (e.g., thermochemical cycle).
- [Partially computable] Can be derived from DFT total energy decomposition, but generally requires direct extraction from the literature or calculations.
"""

Primary_Particle_Size_Distribution = """
Definition: The primary particle size distribution (D10, D50, D90), in nm.
Extraction requirements:
- Output as a dictionary, e.g., {"D10": 150, "D50": 200, "D90": 300, "unit": "nm"}.
- If only a range is given (e.g., 100–200 nm), use the midpoint and note the range.
- Must specify the measurement method (SEM, TEM, laser diffraction).
"""

Secondary_Particle_Size_Distribution = """
Definition: The secondary particle size distribution (agglomerates), in μm.
Extraction requirements:
- Output as a dictionary, e.g., {"D10": 5, "D50": 10, "D90": 15, "unit": "μm"}.
- Method: laser diffraction particle size analysis.
"""

Electrode_Pore_Size_Distribution = """
Definition: The electrode pore size distribution expressed as volume or area fractions for micropores (<2 nm), mesopores (2–50 nm), and macropores (>50 nm).
Extraction requirements:
- Output as a dictionary, e.g., {"micropore_vol%": 10, "mesopore_vol%": 70, "macropore_vol%": 20}.
- Specify the measurement method (BET/BJH, MIP).
"""

Surface_Spinel_Layer_Thickness = """
Definition: The thickness of a spinel or rock-salt degradation layer formed on the surface of layered cathode materials, in nm.
Extraction requirements:
- Unit in nm.
- Must specify the measurement method (HRTEM, HAADF-STEM).
- Note the cycle number or state (pristine / after cycling).
"""

XPS_ROCO2Li_Peak = """
Definition: The peak intensity of ROCO₂Li (alkyl carbonate lithium) in the XPS C 1s spectrum at approximately 290.0 eV.
Extraction requirements:
- Output as a numerical value (peak area, atomic percentage, or intensity ratio).
- Specify the unit (at%, area%, or intensity ratio).
- Note the cycle number or state.
"""

XPS_C_O_Peak = """
Definition: The C–O peak in the XPS C 1s spectrum at approximately 286.0–286.5 eV (ether/hydroxyl carbon).
Extraction requirements:
- Same as XPS_ROCO2Li_Peak.
"""

XPS_NiF2_Peak = """
Definition: The NiF₂ peak in the XPS Ni 2p (~857.0 eV) and F 1s (~684.0 eV) spectra, indicating surface corrosion and transition metal dissolution.
Extraction requirements:
- Intensity or atomic percentage.
- Specify the cycling conditions.
"""

LixPOyFz = """
Definition: Fluorinated oxygen-containing phosphorus species (decomposition products of LiPF₆), appearing in F 1s (~686–688 eV) or P 2p (~134–137 eV).
Extraction requirements:
- Intensity or atomic percentage.
"""

# ==================== Agent 2: Electrochemical Performance ====================

Electronic_Conductivity_Bulk = """
Definition: The macroscopic electronic conductivity of the cathode composite or pristine material, in S/cm.
Extraction requirements:
- Unit in S/cm.
- Specify the measurement method (four-point probe, two-point DC) and sample form (pellet, coated electrode).
"""

Initial_Coulombic_Efficiency = """
Definition: The first-cycle Coulombic efficiency (%), i.e., first discharge capacity / first charge capacity × 100%.
Extraction requirements:
- Unit in %. If given as a decimal (e.g., 0.85), convert to 85%.
- Must be linked to a condition_id (test conditions).
- Exclude the average CE over subsequent stable cycles.
"""

Discharge_Specific_Capacity_Initial = """
Definition: The first-cycle discharge specific capacity, in mAh/g.
Extraction requirements:
- Unit in mAh/g, based on active material mass.
- Link to condition_id, specifying the voltage window and C-rate.
- If based on total electrode mass, this must be noted in the condition.
"""

Rate_Performance = """
Definition: The rate performance (capacity ratio), e.g., the ratio of capacity at 5C to that at 0.2C (dimensionless).
Extraction requirements:
- Output as a dictionary, with keys representing high C-rates (e.g., "5C") and values as ratios (0–1).
- If given as a percentage, convert to decimal.
- Must be linked to condition_id, specifying the reference low C-rate.
"""

Capacity_Retention_Ratio = """
Definition: The capacity retention (%) after a specified number of cycles.
Extraction requirements:
- Unit in %.
- Must specify the cycle number and test conditions (condition_id).
- Output format: "85.2% @500 cycles" or stored as separate fields.
"""

Rate_Capability_Profile = """
Definition: The rate capability curve data — the discharge specific capacity (mAh/g) at each C-rate.
Extraction requirements:
- Output as a list of items, each containing a rate string and capacity value, e.g., [{"rate": "0.2C", "capacity": 180}, {"rate": "1C", "capacity": 170}].
- If the rate is expressed as current density, attempt to convert to C-rate (requires theoretical capacity); if conversion is not possible, retain the original unit and note.
"""

Nominal_Discharge_Voltage = """
Definition: The nominal discharge voltage (plateau voltage), typically taken as the midpoint voltage or the voltage corresponding to the dQ/dV peak, in V vs. Li⁺/Li.
Extraction requirements:
- Unit in V.
- Specify whether it is the integrated average or the median voltage.
"""

Average_Discharge_Voltage = """
Definition: The average discharge voltage obtained by integrating the voltage–capacity curve, in V.
Extraction requirements:
- Unit in V.
- Must specify the calculation method (integrated average).
"""

Charge_Discharge_Voltage_Gap = """
Definition: The charge–discharge voltage hysteresis (ΔV), i.e., the difference between the charge voltage and discharge voltage at the same SOC, in V.
Extraction requirements:
- Unit in V.
- Specify the SOC point (e.g., 50% SOC) or provide the hysteresis area.
"""

Ion_Diffusion_Coefficient = """
Definition: The chemical diffusion coefficient of Li⁺ (D_Li), in cm²/s. Typically measured by GITT or CV.
Extraction requirements:
- Unit in cm²/s, using scientific notation (e.g., 2.3 × 10⁻¹⁰).
- Specify the test method (GITT / CV / PITT) and the corresponding voltage point or SOC.
"""

Gravimetric_Energy_Density = """
Definition: The gravimetric energy density, in Wh/kg. Can be based on active material or the full cell.
Extraction requirements:
- Unit in Wh/kg.
- Specify the basis ("active_material" or "full_cell").
- If based on the full cell, indicate whether it includes the electrolyte, cell housing, etc.
"""

Volumetric_Energy_Density = """
Definition: The volumetric energy density, in Wh/L.
Extraction requirements:
- Unit in Wh/L.
- Specify the basis ("electrode" or "full_cell").
"""

Charge_Transfer_Resistance = """
Definition: The charge transfer resistance R_ct (Faradaic reaction at the electrode/electrolyte interface), in Ω or Ω·cm². Obtained from EIS fitting. Do NOT fold R_SEI/R_CEI into this label: the surface film resistance has its own label SEI_Resistance.
Extraction requirements:
- Unit in Ω (if area-normalized, specify Ω·cm²).
- Specify the SOC, cycle number, and temperature.
"""

SEI_Resistance = """
Definition: The ionic resistance of the passivation film on the cathode surface (written R_SEI in most papers, strictly R_CEI), extracted from EIS (high-frequency semicircle) or DRT. Alternative names: R_SEI, R_CEI, Surface film resistance.
Extraction requirements:
- Unit: Ω or Ω·cm².
- Must specify the SOC, cycle number, and temperature.
"""

Self_Discharge_Rate = """
Definition: The self-discharge rate, in %/month or %/day.
Extraction requirements:
- Unit in %/month or %/day.
- Specify the SOC and storage temperature.
"""

Thermal_Runaway_Onset_Temperature = """
Definition: The thermal runaway onset temperature T_on, in °C.
Extraction requirements:
- Unit in °C.
- Specify the test method (ARC, DSC) and state of charge.
"""

Phase_Transition_Voltage = """
Definition: The phase transition voltage, in V vs. Li⁺/Li.
Extraction requirements:
- Output as a list, each item pairing a phase transition name with its voltage.
- Specify the phase transition type (e.g., O3 → H1, H1 → H2, H2 → H3).
"""

Transition_Metal_Dissolution_Amount = """
Definition: The amount of transition metal dissolution, in ppm or μg/L.
Extraction requirements:
- Unit in ppm (mg/L) or μg/L.
- Specify the cycle number, voltage window, and detection method (ICP-MS, ICP-OES).
"""

O2_CO2_Evolution = """
Definition: The amount of gas evolved (O₂, CO₂), in μmol/g or relative intensity.
Extraction requirements:
- Output as a dictionary, e.g., {"O2": 2.3, "CO2": 1.5, "unit": "μmol/g"}.
- Specify the test method (DEMS, GC) and the upper cutoff voltage.
"""

# ==================== Agent 3: Preparation and Test Conditions ====================

Active_Material_Mass_Fraction = """
Definition: The mass fraction of active material in the cathode electrode (wt%), i.e., active material mass / (active material + conductive additive + binder) × 100%.
Extraction requirements:
- Unit in %.
- If the literature provides specific conductive additive and binder ratios, extract those as well.
"""

Electrode_Thickness = """
Definition: The electrode coating thickness (excluding the current collector), in μm.
Extraction requirements:
- Unit in μm (if given in mm, multiply by 1000).
- Specify whether it is single-sided or double-sided coating.
"""

Mass_Loading = """
Definition: The areal mass loading (mass of the active material composite layer per unit area), in mg/cm².
Extraction requirements:
- Unit in mg/cm².
- If the literature provides the areal mass loading of active material only, specify whether it includes conductive additive/binder.
"""

Compacted_Density = """
Definition: The compaction density, in g/cm³. Formula = areal mass loading (mg/cm²) / thickness (μm) × 10.
Extraction requirements:
- Unit in g/cm³.
- If directly reported, extract directly; otherwise, it can be calculated from areal mass loading and thickness.
"""

Conductive_Additive_Binder_Ratio = """
Definition: The mass ratio of conductive additive and binder, e.g., "5% Super P + 5% PVDF".
Extraction requirements:
- Output as a string or dictionary, e.g., {"conductive": 5, "binder": 5, "unit": "wt%"}.
"""

Electronic_Conductivity_Electrode = """
Definition: The electronic conductivity of the composite electrode, in S/cm.
Extraction requirements:
- Unit in S/cm.
- Specify the measurement method (four-point probe).
"""

Peel_Strength = """
Definition: The peel strength, in N/m.
Extraction requirements:
- Unit in N/m.
- Specify the peel angle (90° or 180°).
"""

Electrode_Porosity = """
Definition: The electrode porosity, in %.
Extraction requirements:
- Unit in %.
- Specify the measurement method (mercury intrusion porosimetry / calculation).
"""

Adhesion_Strength = """
Definition: The force required to detach an electrode coating from its current collector, expressed in N/m or MPa.
Extraction requirements:
- Unit: N/m (180° peel test) or MPa (pull-off test). Keep the reported unit.
- Must specify test method and electrode composition (binder content if given).
"""

Mesoscopic_Porosity = """
Definition: The volume fraction of mesopores (2-50 nm) in an electrode or material, expressed in %.
Extraction requirements:
- Unit: %.
- Must specify measurement method (MIP / N2 adsorption) and the sample state (electrode film / powder).
"""

