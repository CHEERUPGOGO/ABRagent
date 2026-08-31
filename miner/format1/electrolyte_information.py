# -*- coding: utf-8 -*-
"""
Electrolyte Extraction Rules

Each tag structure: Definition (including physical meaning / measurement method) + [Extraction requirements].
"""

# ==================== Agent 1: Intrinsic Material Properties ====================

Li_Solvent_Binding_Energy = """
Definition: The binding energy between a Li⁺ ion and a solvent molecule, expressed in eV. It reflects the solvation ability of the solvent toward Li⁺. A lower binding energy facilitates faster desolvation kinetics at the electrode/electrolyte interface. Alternative names: Li⁺-solvent interaction energy, Solvation energy (partial).
Extraction requirements:
- Unit: eV. If given in kJ/mol, divide by 96.485.
- Must specify the solvent molecule and calculation method (e.g., DFT functional, basis set).
- Distinguish between gas-phase and implicit solvation model values.
"""

Li_Anion_Binding_Energy = """
Definition: The binding energy between a Li⁺ ion and an anion (e.g., FSI⁻, TFSI⁻, PF₆⁻), expressed in eV. It influences the degree of ion pairing and aggregate formation in the electrolyte. Alternative names: Li⁺-anion interaction energy, Ion pair binding energy.
Extraction requirements:
- Unit: eV. If given in kJ/mol, divide by 96.485.
- Must specify the anion type and calculation method.
"""

TM_Solvent_Binding_Energy = """
Definition: The binding energy between a transition metal ion (e.g., Mn²⁺, Ni²⁺, Co²⁺) and a solvent molecule, expressed in eV. It indicates the ability of the solvent to suppress transition metal dissolution and subsequent deposition on the anode. Alternative names: Metal‑solvent binding energy.
Extraction requirements:
- Unit: eV. If given in kJ/mol, divide by 96.485.
- Must specify the transition metal ion, its valence state, and the solvent.
"""

Li_Anion_Coordination_Number_MD = """
Definition: The average number of anions (e.g., FSI⁻, PF₆⁻) directly coordinated to a Li⁺ ion in the solvation sheath, as obtained from molecular dynamics (MD) simulations. A higher number favors inorganic‑rich SEI formation. Alternative names: Anion coordination number, CN_anion.
Extraction requirements:
- Unit: dimensionless (floating point number).
- Must specify the simulation conditions (temperature, salt concentration, force field).
"""

Li_Solvent_Coordination_Number_MD = """
Definition: The average number of solvent molecules directly coordinated to a Li⁺ ion in the solvation sheath, obtained from MD simulations. Alternative names: Solvent coordination number, CN_solvent.
Extraction requirements:
- Unit: dimensionless.
- Must specify simulation conditions.
"""

Molecule_Formation_Energy = """
Definition: The energy change when one mole of a molecule (solvent, additive, or salt anion) is formed from its constituent elements in their standard states, expressed in eV per atom or eV per molecule. It reflects the intrinsic thermodynamic stability of the molecule. Alternative names: Heat of formation, ΔH_f.
Extraction requirements:
- Unit: eV/atom or eV/molecule. If given in kJ/mol, divide by 96.485.
- Must specify the reference states and calculation method.
"""

CIP_AGG_Fraction = """
Definition: The fraction of contact ion pairs (CIP, direct Li⁺-anion coordination) and aggregates (AGG, one anion coordinating multiple Li⁺) in the electrolyte, typically measured by Raman spectroscopy deconvolution. Expressed in %. Alternative names: Ion pairing ratio, CIP/AGG population.
Extraction requirements:
- Unit: %.
- Must specify the salt concentration, temperature, and Raman peak assignment.
- If only one fraction is reported (e.g., CIP only), extract that value and note the missing one.
"""

Solvent_van_der_Waals_Volume = """
Definition: The van der Waals volume of a solvent molecule, expressed in cm³/mol. It reflects the steric size of the molecule and affects entropy and solvation dynamics. Alternative names: Molecular volume, V_w.
Extraction requirements:
- Unit: cm³/mol. If given in Å³, multiply by 0.6022 to convert (1 cm³/mol = 1.6605 Å³? careful: 1 Å³ = 1e-24 cm³; 1 cm³/mol = (1e-24 * 6.022e23) = 0.6022 Å³/molecule? Better: use standard conversion: 1 Å³ = 1e-24 cm³; for per mol: value in Å³ × 0.6022 = cm³/mol.)
- Must specify the calculation method (DFT, molecular mechanics) or source database.
"""

Mixing_Entropy = """
Definition: The configurational entropy increase when mixing multiple solvent components, expressed in J mol⁻¹ K⁻¹. ΔS_mix = –R Σ x_i ln x_i, where x_i are mole fractions of solvent components. Higher entropy can stabilize the electrolyte phase. Alternative names: Entropy of mixing, ΔS_mix.
Extraction requirements:
- Unit: J mol⁻¹ K⁻¹.
- 【Calculated tag】If composition is given, pipeline can compute automatically. If literature directly provides the value, extract and note the basis.
"""

Dipole_Moment = """
Definition: The electric dipole moment of a solvent molecule, expressed in Debye (D). It measures molecular polarity and influences solvation power and dielectric constant. Alternative names: μ, Polar moment.
Extraction requirements:
- Unit: Debye (D). 1 D = 3.336×10⁻³⁰ C·m.
- Must specify the calculation or experimental method.
"""

Dielectric_Constant = """
Definition: The relative permittivity (ε_r) of a solvent or electrolyte, a dimensionless quantity that reflects its ability to screen charges. High dielectric constant promotes salt dissociation but often leads to higher desolvation barriers. Alternative names: Relative permittivity, ε_r.
Extraction requirements:
- Unit: dimensionless.
- Must specify the measurement frequency (usually low frequency, e.g., 1 kHz) and temperature.
"""

Fluorination_Degree = """
Definition: The number of fluorine atoms in a molecule or the F/C atomic ratio. Higher fluorination increases oxidative stability and flame retardancy and promotes LiF-rich interfaces. Alternative names: F content, Fluorine substitution count.
Extraction requirements:
- Output as integer (number of F atoms) or float (F/C ratio).
- Must specify which molecule it applies to.
"""

Number_of_Fluorine_Substituents = """
Definition: The number of fluorine atoms or fluorinated groups (–F, –CF₃, –CHF₂) in an organic solvent molecule. Affects HOMO/LUMO levels and interface chemistry. Alternative names: F count, Fluorine substitution number.
Extraction requirements:
- Unit: integer.
- If groups are reported (e.g., two –CF₃), convert to equivalent F atom count (each –CF₃ = 3 F).
"""

HOMO_LUMO_Energy = """
Definition: The highest occupied molecular orbital (HOMO) and lowest unoccupied molecular orbital (LUMO) energies of a solvent or additive molecule, expressed in eV. HOMO energy correlates with oxidative stability (lower HOMO is more stable), and LUMO energy with reductive stability (higher LUMO is more stable). The HOMO–LUMO gap indicates the intrinsic electrochemical stability window. Alternative names: Frontier molecular orbital energies, EHOMO, ELUMO.
Extraction requirements:
- Output as dictionary: {"HOMO": value, "LUMO": value, "gap": value} in eV.
- Must specify the calculation method (e.g., DFT, functional, solvation model).
- If only the gap is reported, extract gap and note missing individual values.
- Do NOT derive or fill in missing values. Only extract what is explicitly reported.
"""

Melting_Point = """
Definition: The temperature at which a solid solvent becomes liquid, expressed in °C. Low melting points enable low-temperature operation. Alternative names: Tm, Fusion temperature.
Extraction requirements:
- Unit: °C. If given in K, subtract 273.15.
- If given as a range, take the midpoint and note the range.
"""

Boiling_Point = """
Definition: The temperature at which a liquid solvent vaporizes at standard atmospheric pressure, expressed in °C. High boiling points reduce vapor pressure and improve safety. Alternative names: Tb, Vaporization temperature.
Extraction requirements:
- Unit: °C. If given in K, subtract 273.15.
"""

Flash_Point = """
Definition: The lowest temperature at which a solvent vapor can ignite in air, expressed in °C. High flash point indicates low flammability. Alternative names: FP, Ignition temperature.
Extraction requirements:
- Unit: °C.
- If multiple values (open cup vs. closed cup), prefer closed cup and note the method.
"""

Viscosity = """
Definition: The dynamic viscosity of a liquid electrolyte or neat solvent, expressed in mPa·s (cP). High viscosity reduces ionic conductivity, especially at low temperature. Alternative names: η, Dynamic viscosity.
Extraction requirements:
- Unit: mPa·s (1 mPa·s = 1 cP). If given in Pa·s, multiply by 1000.
- Must specify temperature.
- If electrolyte, note salt concentration and solvent composition.
"""

Density = """
Definition: The mass per unit volume of a liquid electrolyte or neat solvent, expressed in g/cm³. Used in gravimetric energy density calculations. Alternative names: ρ, Mass density.
Extraction requirements:
- Unit: g/cm³. If given in kg/m³, divide by 1000.
- Must specify temperature and (for electrolyte) salt concentration.
"""


# ==================== Agent 2: Electrochemical Performance ====================

Li_Desolvation_Activation_Energy = """
Definition: The energy barrier required for a Li⁺ ion to shed its solvent sheath upon approaching the electrode surface, expressed in eV. It governs the charge transfer kinetics, especially at low temperatures. Alternative names: Desolvation energy, E_a,desolv.
Extraction requirements:
- Unit: eV. If given in kJ/mol, divide by 96.485.
- Must specify the measurement method (typically temperature‑dependent EIS, Arrhenius fitting).
- Note the electrode material (e.g., graphite, Li metal) and electrolyte composition.
"""

Charge_Transfer_Resistance = """
Definition: The kinetic resistance to the Faradaic reaction Li⁺ + e⁻ ⇌ Li at the electrode/electrolyte interface, expressed in Ω (or Ω·cm² when area‑normalized). Measured by EIS and fitted with an equivalent circuit. Alternative names: R_ct, Interfacial charge transfer resistance.
Extraction requirements:
- Unit: Ω or Ω·cm². If area not given, record as Ω and note missing area.
- Must specify temperature, state of charge (or voltage), and cycle number if cycled.
"""

SEI_Resistance = """
Definition: The ionic resistance of the solid‑electrolyte interphase (SEI) on the anode, extracted from EIS (often high‑frequency semicircle) or distribution of relaxation times (DRT). Alternative names: R_SEI, Surface film resistance.
Extraction requirements:
- Unit: Ω or Ω·cm².
- Must specify the electrode (e.g., Li, graphite) and cycle number.
"""

CEI_Resistance = """
Definition: The ionic resistance of the cathode‑electrolyte interphase (CEI) on the cathode surface, measured by EIS + DRT. Alternative names: R_CEI, Cathode film resistance.
Extraction requirements:
- Unit: Ω or Ω·cm².
- Must specify cathode material and cycle number.
"""

Li_Transport_Activation_Energy_SEI = """
Definition: The activation energy for Li⁺ migration through the SEI, obtained from temperature‑dependent EIS (Arrhenius plot of R_SEI). Expressed in eV. Alternative names: E_a,SEI, SEI ionic conduction barrier.
Extraction requirements:
- Unit: eV. If given in kJ/mol, divide by 96.485.
- Must specify temperature range and measurement method.
"""

Li_Transport_Activation_Energy_CEI = """
Definition: The activation energy for Li⁺ migration through the CEI, from temperature‑dependent EIS of R_CEI. Alternative names: E_a,CEI, CEI ionic conduction barrier.
Extraction requirements:
- Unit: eV. If given in kJ/mol, divide by 96.485.
- Must specify temperature range.
"""

SEI_Thickness = """
Definition: The physical thickness of the solid‑electrolyte interphase on the anode, expressed in nm. Measured by cryogenic transmission electron microscopy (cryo‑TEM) or XPS depth profiling. Alternative names: SEI layer thickness.
Extraction requirements:
- Unit: nm. If given in μm, multiply by 1000.
- Must specify the anode material, cycle number, and measurement method.
"""

CEI_Thickness = """
Definition: The physical thickness of the cathode‑electrolyte interphase on the cathode, expressed in nm. Measured by HRTEM. Alternative names: CEI layer thickness.
Extraction requirements:
- Unit: nm.
- Must specify cathode material and cycle number.
"""

LiF_Content_in_SEI_CEI = """
Definition: The atomic percentage of lithium fluoride (LiF) within the SEI or CEI, determined by XPS depth profiling. Higher LiF content is associated with fast Li⁺ conduction and improved stability. Alternative names: LiF at%, Fluoride content.
Extraction requirements:
- Unit: at%.
- Must specify which interphase (SEI or CEI) and the cycle number.
- If depth profile gives a range, extract the average or the steady‑state value.
"""

Inorganic_Organic_Ratio_SEI_CEI = """
Definition: The ratio of inorganic species (LiF, Li₂O, Li₂S, etc.) to organic species (C–F, C–O, C=O, etc.) in the SEI or CEI, derived from XPS quantification. Higher inorganic ratio usually improves passivation. Alternative names: I/O ratio.
Extraction requirements:
- Unit: dimensionless ratio.
- Must specify which interphase.
- If separate values for each component, extract the ratio.
"""

Li2O_Content_in_SEI = """
Definition: The atomic percentage of lithium oxide (Li₂O) in the SEI, measured by XPS O 1s spectrum. Li₂O contributes to interfacial stability. Alternative names: Li₂O at%.
Extraction requirements:
- Unit: at%.
- Must specify cycle number.
"""

S_N_Content_in_SEI = """
Definition: The atomic percentage of sulfur and nitrogen in the SEI, originating from decomposition of anions (e.g., FSI⁻, TFSI⁻). Indicates the degree of anion‑derived interface. Alternative names: S at%, N at%.
Extraction requirements:
- Output as dictionary: {"S": value, "N": value} in at%.
- Must specify cycle number and electrolyte composition.
"""

Transition_Metal_Deposition = """
Definition: The amount of transition metals (e.g., Mn, Ni, Co) deposited on the SEI or CEI, expressed in at% or µg/g. Originates from cathode dissolution. Alternative names: TM deposition, Metal cross‑talk.
Extraction requirements:
- Unit: at% (XPS) or µg/g (ICP‑MS). If ppm, assume µg/g.
- Must specify which interphase and cycle number.
"""

Interfacial_Crack_Density = """
Definition: The number of cracks per unit length (µm⁻¹) or the area fraction of cracks within secondary cathode particles after cycling. Indicates mechanical degradation. Alternative names: Crack density, Fracture density.
Extraction requirements:
- Unit: µm⁻¹ (line density) or % (area fraction). If given as fraction (0.0–1.0), convert to %.
- Must specify cycle number and imaging method (SEM, FIB‑SEM).
"""

Interface_Roughness = """
Definition: The root‑mean‑square (RMS) roughness of the electrode or SEI surface, measured by atomic force microscopy (AFM), expressed in nm. Higher roughness indicates non‑uniform interfacial reactions. Alternative names: RMS roughness, Surface roughness.
Extraction requirements:
- Unit: nm.
- Must specify the sample state (e.g., pristine, after cycling) and scan area.
"""

Contact_Angle = """
Definition: The angle (θ) formed between a droplet of electrolyte and the surface of a separator or electrode, measured in degrees (°). Lower angle indicates better wettability. Alternative names: Wetting angle.
Extraction requirements:
- Unit: °.
- Must specify the solid substrate (separator type, electrode material) and the test temperature.
- If multiple measurements, report the average and note the standard deviation.
"""

Ionic_Conductivity = """
Definition: The ionic conductivity (σ) of the electrolyte, expressed in mS/cm. Measured by EIS using a blocking cell (e.g., stainless steel electrodes). It depends on temperature, salt concentration, and solvent composition. Alternative names: σ, Electrolyte conductivity.
Extraction requirements:
- Unit: mS/cm. If given in S/cm, multiply by 1000.
- Must specify temperature and measurement method (EIS frequency range).
- If conductivity vs. concentration is given, extract the maximum or the value at the concentration used.
"""

Electrochemical_Stability_Window = """
Definition: The voltage range over which the electrolyte does not undergo significant oxidation or reduction, expressed in V (usually vs. Li⁺/Li). Determined by linear sweep voltammetry (LSV). Alternative names: ESW, Stability window.
Extraction requirements:
- Unit: V.
- Must specify the lower and upper cut‑off voltages (e.g., 0–4.5 V) or the onset potentials.
- Note the working electrode material (e.g., Al, Pt, glassy carbon) because it affects the window.
"""

Anodic_Stability_Onset_Potential = """
Definition: The potential (vs. Li⁺/Li) at which the anodic current rises steeply in LSV, indicating the onset of electrolyte oxidation. A higher potential is better for high‑voltage cathodes. Alternative names: Oxidation onset, E_ox.
Extraction requirements:
- Unit: V vs. Li⁺/Li.
- Must specify the current density criterion (e.g., 0.1 mA/cm²).
- Note the working electrode material.
"""

Reduction_Onset_Potential = """
Definition: The potential (vs. Li⁺/Li) at which the cathodic current increases sharply, indicating electrolyte reduction. A lower potential is better for stability against Li metal or graphite anodes. Alternative names: E_red, Reduction stability.
Extraction requirements:
- Unit: V vs. Li⁺/Li.
- Must specify current density criterion.
"""

Operating_Temperature_Range = """
Definition: The range of ambient temperatures over which the battery can be cycled with acceptable performance (e.g., >80% capacity retention). Expressed in °C. Alternative names: Temperature window.
Extraction requirements:
- Output as string or dictionary: {"min": value, "max": value} in °C.
- Must specify the performance criterion used.
- Only extract if the paragraph explicitly describes battery cycling or cell operation over a temperature range. Do NOT extract from heating experiments, thermal stability tests, TGA/DSC, or decomposition temperature measurements.
"""

Capacity_Retention = """
Definition: The percentage of initial discharge capacity retained after a specified number of cycles. Expressed in % at N cycles. Alternative names: Capacity retention ratio, Cycling stability.
Extraction requirements:
- Unit: %.
- Must specify cycle number and test conditions (current rate, temperature, voltage window).
- Use format: {"value": 85.2, "cycle_number": 500}.
"""

Coulombic_Efficiency = """
Definition: The ratio of discharge capacity to charge capacity for a given cycle, expressed in %. For stable cycles, CE close to 100% indicates high reversibility. Alternative names: CE, Faradaic efficiency.
Extraction requirements:
- Unit: %. If given as decimal (e.g., 0.995), multiply by 100.
- Must specify cycle number (or "average over cycles [x,y]").
- For average CE over stable cycles, also specify the cycle range.
"""

Cycle_Life_80 = """
Definition: The number of charge/discharge cycles required for the discharge capacity to degrade to 80% of its initial value. Alternative names: Cycle life to 80% SOH.
Extraction requirements:
- Unit: cycles (integer).
- Must specify test conditions (current rate, temperature, DOD).
- If a different cutoff is used (e.g., 70%), note the cutoff.
"""

Energy_Density = """
Definition: The total electrical energy stored per unit mass (gravimetric, Wh/kg) or per unit volume (volumetric, Wh/L) of the battery cell. Usually reported at a specific C‑rate. Alternative names: Specific energy, Volumetric energy.
Extraction requirements:
- Output as dictionary: {"gravimetric": {"value": ..., "unit": "Wh/kg"}, "volumetric": {"value": ..., "unit": "Wh/L"}}.
- Must specify the basis (e.g., active material only, full cell including packaging).
- Note the test conditions (C‑rate, temperature).
"""

Maximum_Thermal_Runaway_Temperature = """
Definition: The highest temperature reached during thermal runaway, measured by accelerating rate calorimetry (ARC). Expressed in °C. Alternative names: T_max, Peak runaway temperature.
Extraction requirements:
- Unit: °C.
- Must specify the state of charge (SOC) at the start of the test.
"""

Self_Heating_Onset_Temperature = """
Definition: The temperature at which the battery starts self‑heating at a rate >0.02 °C/min in ARC. Expressed in °C. Alternative names: T_onset, Self‑heating temperature.
Extraction requirements:
- Unit: °C.
- Must specify SOC.
"""

Gas_Evolution_Amount = """
Definition: The quantity of gases (e.g., CO₂, H₂, C₂H₄, O₂) evolved during charge/discharge or thermal abuse, measured by online differential electrochemical mass spectrometry (DEMS). Expressed in nmol per mg of active material or normalized intensity. Alternative names: DEMS gas evolution.
Extraction requirements:
- Output as dictionary: {"CO2": value, "H2": value, ...} with unit (e.g., nmol/mg).
- Must specify the voltage range or cycle number.
"""

Voltage_Hysteresis = """
Definition: The difference between the average charge voltage and average discharge voltage, expressed in V. Caused by kinetic limitations and phase transitions. Alternative names: ΔV, Voltage gap.
Extraction requirements:
- Unit: V.
- Must specify the C‑rate and temperature.
- If given at a specific SOC, note SOC.
"""

Transition_Metal_Dissolution_Concentration = """
Definition: The concentration of transition metal ions (Ni, Co, Mn, etc.) dissolved into the electrolyte after cycling, measured by ICP‑MS. Expressed in mg/L (ppm). Alternative names: TM dissolution, Metal leaching.
Extraction requirements:
- Unit: mg/L (ppm). If given in μg/L, divide by 1000.
- Must specify the cycle number and operating conditions.
- Output as dictionary: {"Ni": value, "Co": value, "Mn": value}.
"""

Rate_Capability = """
Definition: The discharge specific capacities (or capacity retention ratios) at various C‑rates (e.g., 0.1C, 0.5C, 1C, 2C, 5C). Expressed in mAh/g or % relative to a low rate. Alternative names: Rate performance, C‑rate capability.
Extraction requirements:
- Output as list of dictionaries: [{"rate": "0.2C", "capacity": 195, "unit": "mAh/g"}, ...]. Each item has its own unit.
- If capacity ratios are given, convert to capacities if theoretical capacity known; otherwise extract ratios with note.
- Must specify the voltage window and temperature.
"""

DCIR = """
Definition: The direct current internal resistance of a battery, measured by pulse discharge (e.g., 10 s pulse at 1C). Includes ohmic, charge transfer, and diffusion contributions. Expressed in Ω. Alternative names: DC internal resistance, Pulse resistance.
Extraction requirements:
- Unit: Ω.
- Must specify the pulse duration, current, state of charge, and temperature.
- If area‑normalized (Ω·cm²), note electrode area.
"""


# ==================== Agent 3: Preparation and Test Conditions ====================

Lithium_Salt_Type = """
Definition: The lithium salt used in the electrolyte, e.g., LiPF₆, LiTFSI, LiFSI, LiBF₄.
Extraction requirements:
- String.
"""

Salt_Concentration = """
Definition: The molar concentration of lithium salt in the electrolyte, expressed in mol/L (M).
Extraction requirements:
- Unit: mol/L (M). If given in mol/kg (molality), note that.
- Floating point number.
"""

Solvent_Composition = """
Definition: The solvents and their volume ratios, e.g., "EC/DMC = 1:1 (vol%)" or "EC:EMC = 3:7".
Extraction requirements:
- String.
"""

Additives = """
Definition: The additives and their weight percentages (e.g., 2% FEC, 1% VC).
Extraction requirements:
- Output as list of dictionaries: [{"name": "FEC", "wt%": 2}, ...].
"""

Water_Content = """
Definition: The water concentration in the electrolyte, expressed in ppm.
Extraction requirements:
- Unit: ppm (by weight or volume). Must specify.
- Floating point number.
"""

Mixing_Process = """
Definition: Optional description of electrolyte preparation (mixing time, temperature, order of addition).
Extraction requirements:
- String; optional.
"""

Thermal_Conductivity = """
Definition: The intrinsic ability of a material to conduct heat, expressed in W/(m·K).
Extraction requirements:
- Unit: W/(m·K). If given in W/(cm·K), multiply by 100.
- Must specify temperature and measurement method (transient hot-wire / LFA / hot-disk).
"""

Thermal_Diffusivity = """
Definition: The rate at which heat propagates through a material, expressed in m²/s or mm²/s.
Extraction requirements:
- Unit: m²/s (or mm²/s; convert mm²/s to m²/s by dividing by 1e6).
- Must specify temperature and measurement method (LFA / hot-disk).
"""

Specific_Surface_Area = """
Definition: The total surface area per unit mass of a powder or porous material, expressed in m²/g.
Extraction requirements:
- Unit: m²/g. If given in cm²/g, divide by 1e4.
- Must specify measurement method (BET N2 adsorption).
"""
