# -*- coding: utf-8 -*-
"""
Label definitions for lithium‑ion battery electrolytes and related interfacial properties.
"""

# ==================== Agent 1: Material Intrinsic Properties ====================

Li_Solvent_Binding_Energy = """
Lithium‑Solvent Binding Energy is the interaction energy between a Li⁺ ion and a solvent molecule, expressed in eV. It reflects the intrinsic solvation ability of the solvent toward Li⁺. A lower binding energy facilitates faster desolvation kinetics at the electrode/electrolyte interface. Alternative names: Li⁺‑solvent interaction energy, Solvation energy (partial), Binding affinity.
"""

Li_Anion_Binding_Energy = """
Lithium‑Anion Binding Energy is the interaction energy between a Li⁺ ion and an anion (e.g., FSI⁻, TFSI⁻, PF₆⁻), expressed in eV. It influences the degree of ion pairing and aggregate formation in the electrolyte. Higher binding energy promotes contact ion pairs and aggregates, affecting the solvation structure and interfacial chemistry. Alternative names: Li⁺‑anion interaction energy, Ion pair binding energy.
"""

TM_Solvent_Binding_Energy = """
Transition Metal‑Solvent Binding Energy is the binding energy between a transition metal ion (e.g., Mn²⁺, Ni²⁺, Co²⁺) and a solvent molecule, expressed in eV. It indicates the ability of the solvent to suppress transition metal dissolution and subsequent deposition on the anode. A stronger binding energy helps retain metal ions in solution and reduces their migration to the negative electrode. Alternative names: Metal‑solvent binding energy, M²⁺‑solvent affinity.
"""

Li_Anion_Coordination_Number_MD = """
Lithium‑Anion Coordination Number is the average number of anions (e.g., FSI⁻, PF₆⁻) directly coordinated to a Li⁺ ion in the solvation sheath, derived from molecular dynamics (MD) simulations. This dimensionless quantity reflects the degree of anion participation in the first solvation shell. A higher coordination number favors the formation of an inorganic‑rich solid‑electrolyte interphase. Alternative names: Anion coordination number, CN_anion, First‑shell anion count.
"""

Li_Solvent_Coordination_Number_MD = """
Lithium‑Solvent Coordination Number is the average number of solvent molecules directly coordinated to a Li⁺ ion in the solvation sheath, obtained from MD simulations. This dimensionless parameter characterizes the solvation structure and the competition between solvent and anion for Li⁺ coordination. Alternative names: Solvent coordination number, CN_solvent, First‑shell solvent count.
"""

Molecule_Formation_Energy = """
Molecular Formation Energy is the energy change when one mole of a molecule (solvent, additive, or salt anion) is formed from its constituent elements in their standard states, expressed in eV per atom or eV per molecule. It reflects the intrinsic thermodynamic stability of the molecule. A more negative formation energy indicates higher stability. Alternative names: Heat of formation, Enthalpy of formation, ΔH_f.
"""

CIP_AGG_Fraction = """
Contact Ion Pair / Aggregate Fraction is the proportion of contact ion pairs (CIP, direct Li⁺‑anion coordination) and aggregates (AGG, one anion coordinating multiple Li⁺) in the electrolyte, typically measured by Raman spectroscopy deconvolution. These fractions are expressed in % and describe the equilibrium solvation structure. Higher CIP/AGG fractions indicate strong ion pairing and are often associated with anion‑derived interphases. Alternative names: Ion pairing ratio, CIP/AGG population, Aggregation degree.
"""

Solvent_van_der_Waals_Volume = """
Solvent van der Waals Volume is the spatial volume occupied by a solvent molecule, expressed in cm³/mol. It reflects the steric size of the molecule and influences entropy, solvation dynamics, and the permeability of the solvation shell. Alternative names: Molecular volume, V_w, van der Waals volume.
"""

Mixing_Entropy = """
Mixing Entropy (ΔS_mix) is the configurational entropy increase when multiple solvent components are mixed, expressed in J mol⁻¹ K⁻¹. It is calculated as ΔS_mix = –R Σ x_i ln x_i, where x_i are the mole fractions of each solvent component. Higher mixing entropy can stabilize the electrolyte phase and suppress phase separation. Alternative names: Entropy of mixing, Configurational entropy of the solvent mixture.
"""

Dipole_Moment = """
Dipole Moment is a measure of the electrical polarity of a solvent molecule, expressed in Debye (D). It quantifies the separation of positive and negative charge within the molecule. A higher dipole moment generally correlates with a higher dielectric constant and stronger solvation power, but may also increase the desolvation barrier. Alternative names: Molecular dipole, μ, Electric dipole moment.
"""

Dielectric_Constant = """
Dielectric Constant (relative permittivity, ε_r) is a dimensionless quantity that measures a solvent’s ability to screen electric charges. A high dielectric constant promotes salt dissociation and electrolyte conductivity, but often leads to higher desolvation energy barriers. It is a key parameter for predicting ion transport and solvation behavior. Alternative names: Relative permittivity, ε_r, Static permittivity.
"""

Fluorination_Degree = """
Fluorination Degree is the number of fluorine atoms in a molecule or the fluorine‑to‑carbon (F/C) atomic ratio. Higher fluorination increases oxidative stability, flame retardancy, and promotes the formation of a LiF‑rich interphase. It is a key structural parameter for designing high‑voltage and safe electrolytes. Alternative names: F content, Fluorine substitution level, F/C ratio.
"""

Number_of_Fluorine_Substituents = """
Number of Fluorine Substituents is the count of fluorine atoms or fluorinated groups (–F, –CF₃, –CHF₂) in an organic solvent molecule. This integer descriptor directly affects the molecule’s HOMO/LUMO levels, oxidation resistance, and the resulting interphase chemistry. Alternative names: F count, Fluorine substitution number, Degree of fluorination.
"""

HOMO_LUMO_Energy = """
HOMO/LUMO Energy refers to the energies of the highest occupied molecular orbital (HOMO) and the lowest unoccupied molecular orbital (LUMO) of a solvent or additive molecule, expressed in eV. A low HOMO energy indicates high oxidative stability (resistance to electron loss), while a high LUMO energy indicates high reductive stability. The HOMO‑LUMO gap represents the intrinsic electrochemical stability window of the molecule. Alternative names: Frontier molecular orbital energies, EHOMO, ELUMO, Orbital energy levels.
"""

Melting_Point = """
Melting Point is the temperature at which a solid solvent transitions to a liquid state, expressed in °C. A low melting point enables electrolyte operation at low temperatures, while a high melting point can lead to solidification and increased resistance. Alternative names: Tm, Fusion temperature, Liquefaction point.
"""

Boiling_Point = """
Boiling Point is the temperature at which a liquid solvent vaporizes at standard atmospheric pressure, expressed in °C. A high boiling point reduces vapor pressure, minimizes gas evolution, and improves safety at elevated temperatures. Alternative names: Tb, Vaporization temperature, Ebullition point.
"""

Flash_Point = """
Flash Point is the lowest temperature at which a solvent’s vapor can ignite in air, expressed in °C. A high flash point indicates low flammability and safer handling, especially for electrolytes used in electric vehicles and portable electronics. Alternative names: FP, Ignition temperature, Fire point.
"""

Viscosity = """
Viscosity (dynamic viscosity) is a measure of a liquid’s resistance to flow, expressed in mPa·s (cP). For electrolytes, high viscosity reduces ionic conductivity, especially at low temperatures, and hinders electrode wetting. Viscosity is strongly dependent on temperature, salt concentration, and solvent composition. Alternative names: η, Dynamic viscosity, Fluid friction.
"""

Density = """
Density is the mass per unit volume of a liquid electrolyte or neat solvent, expressed in g/cm³. It is a fundamental physical property used in gravimetric energy density calculations and electrode porosity modeling. Density depends on temperature, salt concentration, and solvent composition. Alternative names: ρ, Mass density, Specific gravity.
"""


# ==================== Agent 2: Electrochemical Performance Properties ====================

Li_Desolvation_Activation_Energy = """
Lithium Desolvation Activation Energy is the energy barrier that a Li⁺ ion must overcome to shed its solvent sheath upon approaching the electrode surface, expressed in eV. It governs the charge‑transfer kinetics, particularly at low temperatures, and is a critical parameter for fast‑charging capability. A lower desolvation energy improves rate performance. Alternative names: Desolvation energy, E_a,desolv, Solvent‑stripping barrier.
"""

Charge_Transfer_Resistance = """
Charge Transfer Resistance (R_ct) is the kinetic resistance to the Faradaic reaction Li⁺ + e⁻ ⇌ Li at the electrode/electrolyte interface, expressed in Ω or Ω·cm². It is extracted from electrochemical impedance spectroscopy (EIS) and reflects the ease of electron transfer across the double layer. A low R_ct indicates fast interfacial kinetics. Alternative names: Interfacial charge transfer resistance, R_ct, Faradaic resistance.
"""

SEI_Resistance = """
SEI Resistance (R_SEI) is the ionic resistance of the solid‑electrolyte interphase on the anode, typically extracted from the high‑frequency semicircle in EIS or from distribution of relaxation times (DRT). It reflects the ability of the SEI to conduct Li⁺ ions. A low R_SEI is desirable for high‑rate capability. Alternative names: R_SEI, Surface film resistance, SEI impedance.
"""

CEI_Resistance = """
CEI Resistance (R_CEI) is the ionic resistance of the cathode‑electrolyte interphase on the cathode surface, measured by EIS combined with DRT. It represents the barrier for Li⁺ transport through the passivation layer on the positive electrode. Alternative names: R_CEI, Cathode film resistance, CEI impedance.
"""

Li_Transport_Activation_Energy_SEI = """
Lithium Transport Activation Energy through SEI is the energy barrier for Li⁺ migration within the solid‑electrolyte interphase, expressed in eV. It is obtained from temperature‑dependent EIS measurements of R_SEI using the Arrhenius equation. A lower activation energy indicates a more conductive SEI. Alternative names: E_a,SEI, SEI ionic conduction barrier, Activation energy for SEI transport.
"""

Li_Transport_Activation_Energy_CEI = """
Lithium Transport Activation Energy through CEI is the energy barrier for Li⁺ migration within the cathode‑electrolyte interphase, expressed in eV. Derived from temperature‑dependent EIS of R_CEI, this parameter quantifies the temperature sensitivity of the CEI resistance. Alternative names: E_a,CEI, CEI ionic conduction barrier.
"""

SEI_Thickness = """
SEI Thickness is the physical thickness of the solid‑electrolyte interphase on the anode, expressed in nm. It is measured by cryogenic transmission electron microscopy (cryo‑TEM) or XPS depth profiling. An optimal SEI thickness balances ionic conductivity and passivation effectiveness. Alternative names: SEI layer thickness, Interphase thickness.
"""

CEI_Thickness = """
CEI Thickness is the physical thickness of the cathode‑electrolyte interphase, expressed in nm. It is typically measured by high‑resolution transmission electron microscopy (HRTEM) of cycled cathode particles. A thin, uniform CEI is desirable for low resistance. Alternative names: CEI layer thickness, Cathode interphase thickness.
"""

LiF_Content_in_SEI_CEI = """
LiF Content in SEI/CEI is the atomic percentage of lithium fluoride (LiF) within the solid‑electrolyte interphase or cathode‑electrolyte interphase, determined by XPS depth profiling. High LiF content is associated with fast Li⁺ conduction, mechanical robustness, and improved cycling stability. Alternative names: LiF at%, Fluoride content, LiF proportion.
"""

Inorganic_Organic_Ratio_SEI_CEI = """
Inorganic/Organic Ratio in SEI/CEI is the ratio of inorganic species (such as LiF, Li₂O, Li₂S) to organic species (such as C–F, C–O, C=O, polymeric species) within the interphase, derived from XPS quantification. A higher inorganic ratio typically indicates a more passivating and stable interface. Alternative names: I/O ratio, Inorganic‑to‑organic ratio.
"""

Li2O_Content_in_SEI = """
Li₂O Content in SEI is the atomic percentage of lithium oxide (Li₂O) in the solid‑electrolyte interphase, measured by XPS O 1s spectrum. Li₂O is an inorganic component that contributes to interfacial stability and mechanical integrity. Alternative names: Li₂O at%, Lithium oxide content.
"""

S_N_Content_in_SEI = """
S/N Content in SEI refers to the atomic percentages of sulfur and nitrogen species (e.g., sulfates, sulfides, nitrides) incorporated into the solid‑electrolyte interphase. These elements originate from decomposition of anions such as FSI⁻ or TFSI⁻ and indicate the degree of anion‑derived interphase formation. Alternative names: S at%, N at%, Sulfur/nitrogen content.
"""

Transition_Metal_Deposition = """
Transition Metal Deposition is the amount of transition metals (e.g., Mn, Ni, Co) deposited on the SEI or CEI, expressed in at% (by XPS) or µg/g (by ICP‑MS). These metals originate from cathode dissolution and migrate to the anode, where they disrupt SEI integrity and accelerate capacity fade. Alternative names: TM deposition, Metal cross‑talk, Dissolved metal redeposition.
"""

Interfacial_Crack_Density = """
Interfacial Crack Density is the number of cracks per unit length (µm⁻¹) or the area fraction of cracks within secondary cathode particles after cycling. It quantifies the mechanical degradation induced by volume changes and anisotropic lattice expansion. Higher crack density correlates with accelerated capacity fading and impedance rise. Alternative names: Crack density, Fracture density, Particle cracking degree.
"""

Interface_Roughness = """
Interface Roughness is the root‑mean‑square (RMS) roughness of the electrode or SEI surface, measured by atomic force microscopy (AFM), expressed in nm. It reflects the uniformity of the interphase and the extent of parasitic reactions. A rougher interface often indicates non‑uniform SEI growth or dendrite formation. Alternative names: RMS roughness, Surface roughness, Topographic roughness.
"""

Contact_Angle = """
Contact Angle is the angle (θ) formed by a droplet of electrolyte on the surface of a separator or electrode, measured in degrees (°). A lower contact angle indicates better wettability, which ensures uniform electrolyte distribution, efficient ion transport, and low interfacial resistance. Alternative names: Wetting angle, θ, Sessile drop angle.
"""

Ionic_Conductivity = """
Ionic Conductivity (σ) is the ability of an electrolyte to transport ions, expressed in mS/cm. It is measured by electrochemical impedance spectroscopy using a blocking cell (e.g., stainless steel electrodes). Ionic conductivity depends strongly on temperature, salt concentration, and solvent composition, and is a key parameter for rate capability. Alternative names: Electrolyte conductivity, σ, Specific conductance.
"""

Electrochemical_Stability_Window = """
Electrochemical Stability Window (ESW) is the voltage range over which the electrolyte does not undergo significant oxidation or reduction, expressed in V (typically vs. Li⁺/Li). It is determined by linear sweep voltammetry (LSW) or cyclic voltammetry (CV). A wide ESW is essential for high‑voltage cathodes and stable anode operation. Alternative names: ESW, Stability window, Electrochemical window.
"""

Anodic_Stability_Onset_Potential = """
Anodic Stability Onset Potential is the potential (vs. Li⁺/Li) at which the anodic current rises steeply in linear sweep voltammetry (LSV), indicating the onset of electrolyte oxidation. A higher potential is desirable for compatibility with high‑voltage cathodes. Alternative names: Oxidation onset, E_ox, Anodic decomposition potential.
"""

Reduction_Onset_Potential = """
Reduction Onset Potential is the potential (vs. Li⁺/Li) at which the cathodic current increases sharply during LSV or CV, indicating the start of electrolyte reduction. A lower potential (more negative) is beneficial for stability against lithium metal or graphitic anodes. Alternative names: E_red, Reduction stability, Cathodic decomposition potential.
"""

Operating_Temperature_Range = """
Operating Temperature Range is the range of ambient temperatures over which the battery can be cycled with acceptable performance (e.g., >80% capacity retention), expressed in °C. It defines the practical thermal limits of the electrolyte and the full cell. Alternative names: Temperature window, Working temperature range.
"""

Capacity_Retention = """
Capacity Retention is the percentage of initial discharge capacity that remains after a specified number of charge/discharge cycles. It is a direct measure of long‑term cycling stability and is influenced by electrolyte degradation, interphase evolution, and mechanical integrity. Alternative names: Capacity retention ratio, Cycling stability, Capacity fade, 容量保持率, 循环保持率.
"""

Coulombic_Efficiency = """
Coulombic Efficiency (CE) is the ratio of discharge capacity to charge capacity for a given cycle, expressed in %. For an ideal reversible reaction, CE = 100%. In practice, CE < 100% indicates parasitic reactions, lithium inventory loss, or irreversible side processes. High and stable CE is a key indicator of electrolyte compatibility and interphase passivation. Alternative names: CE, Faradaic efficiency, Charge/discharge efficiency, 库仑效率, 库伦效率.
"""

Cycle_Life_80 = """
Cycle Life (to 80% capacity) is the number of charge/discharge cycles a battery can undergo before its discharge capacity decays to 80% of its initial value. It is a benchmark for practical battery durability and reflects the combined stability of the electrolyte, electrodes, and interphases. Alternative names: Cycle life to 80% SOH, Service life, Cycle durability.
"""

Energy_Density = """
Energy Density is the total electrical energy stored per unit mass (gravimetric, Wh/kg) or per unit volume (volumetric, Wh/L) of the battery cell. It depends on the specific capacities and operating voltages of the electrodes, as well as the electrolyte and auxiliary components. Higher energy density is the primary target for electric vehicles and portable electronics. Alternative names: Specific energy (gravimetric), Volumetric energy, Cell‑level energy density.
"""

Maximum_Thermal_Runaway_Temperature = """
Maximum Thermal Runaway Temperature is the highest temperature reached during a thermal runaway event, measured by accelerating rate calorimetry (ARC), expressed in °C. It indicates the severity of thermal failure and is influenced by the exothermic reactions of the electrolyte with charged electrodes. Alternative names: T_max, Peak runaway temperature, Maximum cell temperature.
"""

Self_Heating_Onset_Temperature = """
Self‑Heating Onset Temperature is the temperature at which the battery starts to self‑heat at a rate exceeding 0.02 °C/min in ARC, expressed in °C. It marks the beginning of thermal runaway propagation and is a critical safety parameter for electrolyte design. Alternative names: T_onset, Self‑heating temperature, Decomposition onset temperature.
"""

Gas_Evolution_Amount = """
Gas Evolution Amount is the quantity of gaseous species (e.g., CO₂, H₂, C₂H₄, O₂) produced during charge/discharge or thermal abuse, measured by online differential electrochemical mass spectrometry (DEMS). It is expressed in nmol per mg of active material or as normalized intensity. Gas evolution correlates with electrolyte decomposition and interphase formation. Alternative names: DEMS gas evolution, Volumetric gas release, Outgassing amount.
"""

Voltage_Hysteresis = """
Voltage Hysteresis (ΔV) is the difference between the average charge voltage and average discharge voltage at a given capacity, expressed in V. It arises from kinetic limitations, phase transitions, or structural rearrangements. A large hysteresis reduces energy efficiency and indicates poor reversibility. Alternative names: Voltage gap, Polarization, Charge‑discharge overpotential.
"""

Transition_Metal_Dissolution_Concentration = """
Transition Metal Dissolution Concentration is the concentration of transition metal ions (e.g., Ni, Co, Mn) dissolved into the electrolyte after cycling, expressed in mg/L (ppm). It is measured by ICP‑MS and reflects the chemical and structural stability of the cathode. High dissolution levels lead to anode contamination and rapid capacity fade. Alternative names: TM dissolution, Metal leaching, Cation dissolution amount.
"""

Rate_Capability = """
Rate Capability is the ability of a battery to deliver discharge capacity at various current rates (C‑rates), typically expressed as specific capacity (mAh/g) or as a percentage of the low‑rate capacity. It characterizes the power performance and is influenced by ionic conductivity, charge transfer kinetics, and ion diffusion within electrodes. Alternative names: Rate performance, C‑rate capability, High‑rate dischargeability.
"""

DCIR = """
Direct Current Internal Resistance (DCIR) is the total internal resistance of a battery under direct current conditions, measured by pulse discharge (e.g., 10 s pulse at 1C), expressed in Ω. It includes contributions from ohmic resistance (bulk electrolyte, contacts), charge transfer resistance, and diffusion resistance. DCIR is a practical metric for state‑of‑health and power capability. Alternative names: DC internal resistance, Pulse resistance, Ohmic‑plus‑polarization resistance.
"""


# ==================== Agent 3: Preparation and Testing Conditions ====================
Lithium_Salt_Type = """
Lithium Salt Type identifies the lithium salt used in the electrolyte, such as LiPF₆, LiTFSI, LiFSI, LiBF₄, LiClO₄, or LiDFOB. The salt type determines ionic conductivity, thermal stability, and the composition of the resulting interphases. Alternative names: Li salt, Salt species, Conducting salt.
"""

Salt_Concentration = """
Salt Concentration is the molar concentration of the lithium salt in the electrolyte, expressed in mol/L (M). It affects ionic conductivity, viscosity, solvation structure, and interphase chemistry. High salt concentration (e.g., >3 M) leads to “water‑in‑salt” or “solvent‑in‑salt” effects. Alternative names: Salt molarity, Lithium salt concentration, C_salt.
"""

Solvent_Composition = """
Solvent Composition defines the types and volume ratios of organic solvents used in the electrolyte, such as EC, DMC, EMC, DEC, PC, DOL, DME, or FEC. The composition strongly influences solvation ability, viscosity, dielectric constant, and the resulting interphase properties. Alternative names: Solvent mixture, Solvent blend, Cosolvent ratios.
"""

Additives = """
Additives are chemical compounds added in small amounts (typically 0.1–5 wt%) to the electrolyte to improve specific properties, such as forming a stable SEI/CEI (e.g., FEC, VC), increasing flame retardancy (e.g., TMP), or preventing overcharge. Each additive is identified by its name and weight percentage. Alternative names: Electrolyte additives, Functional additives, Film‑forming agents.
"""

Water_Content = """
Water Content is the concentration of water impurity in the electrolyte, expressed in parts per million (ppm). Water reacts with LiPF₆ to form HF, which corrodes electrodes and accelerates capacity fade. Low water content (<10 ppm) is essential for high‑voltage stability. Alternative names: Moisture content, H₂O concentration, Water impurity.
"""

Mixing_Process = """
Mixing Process optionally describes the preparation procedure of the electrolyte, including mixing time, temperature, order of addition of solvents and salt, and any stirring or agitation methods. It is recorded for reproducibility. Alternative names: Electrolyte preparation, Mixing protocol, Formulation procedure.
"""

Thermal_Conductivity = """
Thermal Conductivity is the intrinsic ability of a material (electrolyte liquid, electrode film, or separator) to conduct heat, expressed in W/(m·K). It governs heat dissipation and thermal runaway behavior in battery cells. Measured by transient hot-wire method (liquids) or laser flash analysis (solid films). Alternative names: heat conductivity, thermal conductivity coefficient, λ, κ.
"""

Thermal_Diffusivity = """
Thermal Diffusivity is the rate at which heat propagates through a material, expressed in m²/s or mm²/s. It links thermal conductivity, density, and specific heat capacity (α = κ/(ρ·cp)) and is typically measured by laser flash analysis (LFA). Alternative names: heat diffusivity, thermal diffusion coefficient, α.
"""

Specific_Surface_Area = """
Specific Surface Area is the total surface area per unit mass of a powder or porous material, expressed in m²/g, typically measured by nitrogen adsorption (BET method). It governs interfacial reaction area and is critical for electrode active materials, conductive additives, and separator coatings. Alternative names: BET surface area, SSA.
"""
