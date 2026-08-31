"""
Label definitions for lithium-ion battery cathode materials and cell performance parameters.
"""

# ==================== Agent 1: Material Intrinsic Properties ====================

Lattice_Parameters = """
Lattice Parameters are the geometric quantities defining the shape and size of the unit cell in the cathode crystal structure, including the three edge lengths (a, b, c) and three interaxial angles (α, β, γ). Units are typically Å for lengths and degrees for angles. For cathode materials such as layered oxides (e.g., NMC, LiCoO₂), spinels (LiMn₂O₄), or olivines (LiFePO₄), lattice parameters evolve upon lithium extraction/insertion, reflecting structural changes and phase transitions. Accurate lattice parameters are essential for understanding the host framework, lithium diffusion pathways, and mechanical stability during cycling. Alternative names: Unit cell dimensions, Crystallographic lattice constants.
"""

Crystal_Space_Group = """
Crystal Space Group (Space Group) is the complete set of symmetry operations (translations, rotations, mirrors, screw axes, and glide planes) that define the symmetry of the cathode crystal structure. It is denoted by symbols such as R‑3m (layered oxides, e.g., LiCoO₂, NMC), Fd‑3m (spinel, e.g., LiMn₂O₄), or Pnma (olivine, e.g., LiFePO₄). The space group determines the topology of lithium diffusion channels, the ordering of transition metals and lithium, and governs phase transition pathways during electrochemical cycling. Alternative names: Crystallographic space group, Symmetry group.
"""

Lithium_Ion_Diffusion_Activation_Energy = """
Lithium Ion Diffusion Activation Energy is the minimum energy that a lithium ion must overcome to hop between adjacent stable sites within the cathode crystal lattice, expressed in eV. It is typically obtained from density functional theory (DFT) calculations using the nudged elastic band (NEB) method or from temperature-dependent electrochemical techniques (e.g., GITT or EIS) via Arrhenius fitting. A lower activation energy indicates faster lithium diffusion and better rate capability. For layered oxides, the activation energy for in-plane diffusion is typically low (0.1–0.3 eV), while for olivine LiFePO₄ it is anisotropic and higher along certain directions. Alternative names: Li diffusion barrier, Activation energy for Li migration.
"""

Li_Ion_Migration_Barrier = """
Lithium Ion Migration Barrier is the energy barrier that a lithium ion must overcome when moving from one crystallographic site to an adjacent site along a specific diffusion path (e.g., octahedral → tetrahedral → octahedral in layered oxides). It is expressed in eV and is essentially synonymous with the diffusion activation energy but often emphasizes the local energy barrier along a defined trajectory. A lower migration barrier promotes high-rate capability. Alternative names: Li hopping barrier, Migration energy barrier.
"""

Electronic_Band_Gap = """
Electronic Band Gap is the energy difference between the conduction band minimum (CBM) and the valence band maximum (VBM) of the cathode material, expressed in eV. The band gap determines the intrinsic electronic conductivity: a zero or small band gap indicates metallic or semimetallic behavior (e.g., NMC, LiCoO₂), whereas a large band gap indicates poor electronic conductivity (e.g., LiFePO₄ ~3.8 eV, LiMn₂O₄ ~2.5 eV). For wide-band-gap cathodes, carbon coating or doping is required to enhance rate capability. Alternative names: Energy gap, Electronic band structure gap.
"""

Theoretical_Specific_Capacity = """
Theoretical Specific Capacity is the maximum charge per unit mass that the cathode material can deliver when all lithium ions are extracted (or all redox centers participate) based on Faraday’s law, expressed in mAh/g. It is calculated as Q_th = (n × F) / (3.6 × M), where n is the number of lithium ions extracted per formula unit (or electrons transferred), F is the Faraday constant (96,485 C/mol), and M is the molar mass. For example, LiCoO₂ (n=0.5) → 274 mAh/g, LiFePO₄ (n=1) → 170 mAh/g, LiMn₂O₄ (n=1) → 148 mAh/g, and NMC811 (n≈0.6–0.7) → ~200–220 mAh/g. Alternative names: Theoretical capacity, Maximum specific capacity.
"""

Formation_Energy = """
Formation Energy is the enthalpy change when one mole of a cathode material compound is formed from its constituent elements in their standard states under standard conditions, expressed in eV per formula unit or kJ/mol. A negative formation energy indicates thermodynamic stability relative to the elements. This parameter is crucial for assessing the synthesizability and intrinsic stability of cathode materials. It is commonly obtained from DFT calculations (e.g., Materials Project database) or experimentally from high-temperature calorimetry. Alternative names: Heat of formation, Enthalpy of formation.
"""

Volume_Change_Ratio = """
Unit Cell Volume Change upon (De)lithiation is the percent change in the unit cell volume when the cathode material transforms from its fully lithiated state to its fully delithiated state (or vice versa), expressed in %. For most insertion cathodes, volume changes are relatively small (<5% for layered NMC, ~7% for LiFePO₄, <1% for spinel LiMn₂O₄), which contributes to good cycle life. Large volume changes cause mechanical degradation, particle cracking, and capacity fading. This parameter is determined by in situ or ex situ XRD refinement. Alternative names: Volume expansion ratio, Lattice volume change, Delithiation-induced volume change.
"""

a_c_axis_expansion = """
a‑ and c‑axis Expansion refers to the anisotropic changes in lattice parameters during charging (delithiation) of layered cathode materials. Typically, the c‑axis expands while the a‑axis contracts upon lithium removal, due to increased electrostatic repulsion between oxygen layers (c‑axis) and enhanced transition metal–oxygen covalency (a‑axis). The reverse occurs upon lithiation. These changes are quantified as Δa (Å), Δc (Å), and ΔV (Å³ or %). Anisotropic expansion can cause microcracking along grain boundaries, especially for Ni‑rich layered oxides. Alternative names: Anisotropic lattice expansion, c‑axis elongation, a‑axis contraction.
"""

Oxygen_Vacancy_Concentration = """
Oxygen Vacancy Concentration is the number density of missing oxygen atoms per unit volume or per formula unit in the cathode crystal lattice. Oxygen vacancies can enhance electronic conductivity and modify the redox behavior (e.g., triggering anionic redox activity), but excessive vacancies lead to structural instability, oxygen release, and capacity fading. It is often expressed as a molar fraction (e.g., LiCoO₂₋ₓ). Alternative names: Oxygen deficiency, Vacancy density.
"""

Oxygen_Vacancy_Formation_Energy = """
Oxygen Vacancy Formation Energy is the energy required to remove one neutral oxygen atom from a perfect crystal lattice and create an oxygen vacancy, expressed in eV. A low formation energy indicates that oxygen vacancies are easily generated, which may promote oxygen redox activity but also compromise structural integrity. This energy is typically calculated via DFT (taking into account oxygen chemical potential) and can be experimentally inferred from temperature‑ and oxygen partial pressure‑dependent conductivity measurements. Alternative names: Oxygen defect formation energy, Vacancy creation energy.
"""

Transition_Metal_Migration_Energy_Barrier = """
Transition Metal Migration Energy Barrier is the minimum energy that a transition metal ion (e.g., Ni, Co, Mn, Fe) must overcome to move from its original crystallographic site to an adjacent site (e.g., from the transition metal layer to the lithium layer in layered oxides), expressed in eV. A high migration barrier suppresses unwanted cation mixing (e.g., Ni/Li exchange) and phase transformations (e.g., layered → spinel → rock salt), thereby maintaining structural order and cycling stability. This barrier is typically calculated using DFT+NEB and can be experimentally validated by STEM observation of cation disorder. Alternative names: Cation migration barrier, TM hopping barrier.
"""

Interlayer_Spacing_of_TM_Layers = """
Interlayer Spacing of Transition Metal Layers is the perpendicular distance between adjacent transition metal (TM) layers in layered cathode materials, expressed in Å. This spacing effectively includes the lithium layer and the two adjacent oxygen layers. It is often approximated by the d‑spacing of the (003) reflection (or equivalent) in XRD patterns. Larger interlayer spacing facilitates faster lithium intercalation/deintercalation, while smaller spacing may hinder diffusion but could improve structural density. Alternative names: TM layer separation, d(003) spacing.
"""

Density_of_States_at_Fermi_Level = """
Density of States at the Fermi Level, denoted N(E_F), is the number of electronic states per unit energy interval at the Fermi energy, expressed in states·eV⁻¹·cell⁻¹ or states·eV⁻¹·atom⁻¹. A non‑zero N(E_F) indicates metallic electronic conductivity (e.g., layered oxides), whereas a zero value corresponds to a semiconductor or insulator (e.g., LiFePO₄). This quantity influences the electron contribution to specific heat, Pauli paramagnetism, and the material’s intrinsic electronic transport. Alternative names: DOS at Fermi level, N(E_F).
"""

Bader_Charge = """
Bader Charge is an atomic charge derived from topological analysis of the electron density (Bader quantum theory of atoms in molecules). It quantifies the actual electron transfer between atoms in a solid, providing insights into the ionicity/covalency of metal‑oxygen bonds. For cathode materials, Bader charges on transition metals and oxygen reveal the degree of charge transfer upon lithiation/delithiation and help rationalize redox mechanisms. Values are expressed in units of elementary charge (e). Alternative names: Bader effective charge, Q(Bader).
"""

Chemical_Composition_Mole_Fractions = """
Chemical Composition (Mole Fractions) refers to the molar ratios of each metal element (e.g., Li, Ni, Co, Mn, Fe, Al) in the cathode material, excluding oxygen. It is typically expressed as an array of floating-point numbers that sum to 1 (e.g., [0.33, 0.33, 0.33] for equimolar Ni, Co, Mn). This composition serves as the fundamental input for all composition‑based descriptors and determines the theoretical capacity, voltage, and stability. Alternative names: Elemental composition, Stoichiometric ratios.
"""

Element_Valence_State = """
Element Valence State (Oxidation State) is the effective charge number of a transition metal (e.g., Ni²⁺, Ni³⁺, Ni⁴⁺, Co³⁺, Co⁴⁺, Mn³⁺, Mn⁴⁺, Fe²⁺, Fe³⁺) in the cathode material either in the as‑synthesized state or at different states of charge. Valence states determine the charge compensation mechanism (cationic vs. anionic redox) and the average voltage. They are typically measured by X‑ray photoelectron spectroscopy (XPS, surface sensitive), hard X‑ray absorption near‑edge structure (XANES, bulk sensitive), or soft X‑ray absorption spectroscopy (sXAS, probing oxygen electronic structure). Alternative names: Oxidation state, Redox state.
"""

Jahn_Teller_Active_Ion_Content = """
Jahn‑Teller Active Ion Content is the mole fraction of transition metal ions that exhibit a Jahn‑Teller distortion, such as Mn³⁺ (high‑spin d⁴), Ni³⁺ (low‑spin d⁷), or Cu²⁺ (d⁹). These ions cause local lattice distortions, leading to anisotropic strain, particle cracking, and accelerated capacity fading, especially in spinel cathodes (e.g., Mn³⁺ in LiMn₂O₄). This content is calculated from the chemical composition and the valence states confirmed by XANES. Alternative names: JT‑active fraction, Jahn‑Teller ion proportion.
"""

devtE = """
devtE (Mean Absolute Deviation of Total Valence Electron) is the mean absolute deviation of the total valence electron counts among the constituent elements in the cathode material, weighted by their mole fractions. It quantifies the compositional heterogeneity of valence electron configuration. This descriptor has been identified as highly important for predicting average voltage and other electrochemical properties. It is dimensionless and is typically computed using elemental descriptors (e.g., via Matminer). Alternative names: Total valence electron deviation, Valence electron dispersion.
"""

VEd = """
VEd (Average d‑orbital Electron Count) is the weighted average number of d electrons of all transition metals in the cathode material, taking into account their specific valence states and mole fractions. For example, Ni²⁺ (d⁸), Co³⁺ (d⁶ low‑spin), Mn⁴⁺ (d³). This parameter influences crystal field stabilization, redox potential, and electronic conductivity. It is dimensionless and is typically calculated using composition‑based descriptors. Alternative names: Average d‑electron count, Mean d‑band filling.
"""

Average_Electron_Affinity = """
Average Electron Affinity is the weighted average (by mole fraction) of the electron affinities of the metal elements in the cathode material, expressed in eV. Electron affinity reflects the energy released when an isolated atom gains an electron. A higher average electron affinity may correlate with higher average redox potential. This descriptor is obtained from elemental databases (e.g., Mendeleev) and is used in machine‑learning models for property prediction. Alternative names: Mean electron affinity, Composition‑weighted electron affinity.
"""

Average_Deviation_of_Ionic_Radius = """
Average Deviation of Ionic Radius (typically the standard deviation or mean absolute deviation) is a statistical measure of the spread of ionic radii of the metal cations (considering their specific valence states) within the cathode material, expressed in pm. A larger deviation indicates greater mismatch in ionic sizes, which can lead to local lattice strain, site disorder, and potentially enhanced configurational entropy if the material is designed as a high‑entropy oxide. This parameter is calculated using Shannon ionic radii. Alternative names: Ionic radius dispersion, Radius mismatch.
"""

Average_Ionization_Energy = """
Average Ionization Energy is the weighted average (by mole fraction) of the first ionization energies of the metal elements in the cathode material, expressed in eV. Ionization energy is the energy required to remove the most loosely bound electron from a gaseous atom. This descriptor correlates with the redox potential and chemical stability of the cathode. It is typically computed from elemental databases. Alternative names: Mean ionization energy, Composition‑weighted ionization potential.
"""

Configurational_Entropy = """
Configurational Entropy is a thermodynamic quantity that measures the degree of random mixing of different elements on equivalent crystallographic sites, expressed in J/(mol·K) or multiples of the gas constant R (≈8.314 J/(mol·K)). For cathode materials, especially high‑entropy oxides, the configurational entropy is calculated as S_config = –R ∑ x_i ln x_i, where x_i is the mole fraction of each metal species on the transition metal site (or lithium site). A configurational entropy > 1.5R is often considered “high entropy”, which can stabilize single‑phase solid solutions and suppress undesirable phase transitions. Alternative names: Mixing entropy, S_config.
"""

Valence_Electron_Count = """
Valence Electron Count is the total number of valence electrons for a given element (e.g., Ni: 10, Co: 9, Fe: 8, Mn: 7, Li: 1, Ti: 4). In the context of cathode materials, the valence electron count of transition metals, along with its statistical moments (e.g., devtE), influences the average voltage and electronic structure. For a compound, the weighted average valence electron count is often used. Alternative names: Valence electrons, VE count.
"""

d_Electron_Configuration_Type = """
d Electron Configuration Type categorizes transition metal ions according to their d‑orbital electron filling pattern. Key types include d⁰ (e.g., Ti⁴⁺, V⁵⁺), d¹⁰ (e.g., Cu⁺, Zn²⁺), and s‑block ions (Li⁺, Mg²⁺) which are Jahn‑Teller inactive and tend to stabilize layered or spinel structures. Other types (d³, d⁶ low‑spin, d⁷ low‑spin, d⁸, etc.) have different crystal field preferences. This classification helps rationalize structural stability, Jahn‑Teller effects, and redox activity. Alternative names: d‑orbital configuration, Electronic configuration type.
"""

Li_Ni_mixing_ratio = """
Li/Ni Mixing Ratio (Cation Mixing) is the atomic percentage of nickel ions (typically Ni²⁺) that occupy lithium sites in the lithium layer of layered oxide cathodes (e.g., NMC, NCA, LiCoO₂ with Ni substitution). Conversely, some lithium may occupy transition metal sites. It is usually expressed as a percentage (e.g., 3% Ni in Li layer) or as the occupancy fraction. A high mixing ratio indicates reduced structural ordering, slower lithium diffusion, and poorer electrochemical performance (especially rate capability and cycle life). It is commonly quantified by Rietveld refinement of XRD or neutron diffraction data, or estimated from the intensity ratio I(003)/I(104) in X‑ray diffractograms. Alternative names: Cation mixing, Ni/Li antisite disorder.
"""

Metal_Oxygen_Bond_Energy = """
Metal‑Oxygen Bond Energy is the average energy required to break a metal‑oxygen bond in the cathode material, expressed in eV or kJ/mol. It reflects the strength of the M‑O interaction, which influences structural stability, thermal stability, and the reversibility of oxygen redox. Higher bond energies generally improve cyclability and safety but may suppress anionic redox activity. This quantity can be estimated from DFT‑calculated total energies or from experimental thermochemical cycles. Alternative names: M‑O bond strength, Average bond dissociation energy.
"""

Primary_Particle_Size_Distribution = """
Primary Particle Size Distribution describes the distribution of the smallest indivisible crystalline domains (single crystals or sub‑grains) within the cathode material, usually expressed as D10, D50, D90 (in nm) or as a histogram. Primary particle size affects lithium solid‑state diffusion length (smaller particles → shorter diffusion path → better rate capability) and mechanical stress accommodation. It is measured by scanning electron microscopy (SEM), transmission electron microscopy (TEM), or laser diffraction if particles are well‑dispersed. Alternative names: Crystallite size distribution, Grain size distribution (primary).
"""

Secondary_Particle_Size_Distribution = """
Secondary Particle Size Distribution describes the size distribution of agglomerates or polycrystalline particles formed by aggregation/sintering of primary particles, typical for commercial cathode materials (e.g., NMC, LCO). It is expressed as D10, D50, D90 (in μm) and influences electrode packing density, slurry rheology, and electrolyte infiltration. Large secondary particles may crack during cycling due to anisotropic volume changes. It is measured by laser diffraction or SEM image analysis. Alternative names: Agglomerate size distribution, Powder particle size distribution.
"""

Electrode_Pore_Size_Distribution = """
Electrode Pore Size Distribution describes the volume (or surface area) fraction of pores as a function of pore diameter within the porous cathode electrode coating. Pores are typically classified as micropores (<2 nm), mesopores (2–50 nm), and macropores (>50 nm). The distribution affects electrolyte wettability, ionic transport, and mechanical integrity. Optimal pore size distribution balances high energy density (low porosity) and good rate capability (sufficient mesopores). It is measured by mercury intrusion porosimetry (MIP) for macro/mesopores or nitrogen sorption (BJH) for meso/micropores. Alternative names: Pore size distribution (PSD), Porosity distribution.
"""

Surface_Spinel_Layer_Thickness = """
Surface Spinel Layer Thickness is the thickness of a degraded surface layer on layered oxide cathodes (e.g., NMC, LiCoO₂) that transforms from the original layered structure into a spinel‑like or rock‑salt phase. This layer forms due to oxygen loss, transition metal migration, and electrolyte attack, and it typically has poor lithium‑ion conductivity, increasing interfacial resistance. Thickness is expressed in nm and is measured by high‑resolution transmission electron microscopy (HRTEM) with fast Fourier transform (FFT) analysis. Alternative names: Surface degradation layer thickness, Spinel phase thickness.
"""

XPS_ROCO2Li_Peak = """
XPS ROCO₂Li Peak refers to the signal from lithium alkyl carbonates (ROCO₂Li) in the X‑ray photoelectron spectroscopy (XPS) C 1s spectrum, typically centered at ~290.0 eV. These species are decomposition products of carbonate‑based electrolytes and are characteristic organic components of the cathode–electrolyte interphase (CEI). The peak intensity (area or atomic percent) reflects the extent of electrolyte oxidation and decomposition on the cathode surface. Alternative names: Alkyl carbonate peak, ROCO₂Li signal.
"""

XPS_C_O_Peak = """
XPS C–O Peak is the signal from carbon singly bonded to oxygen (C–O) in the C 1s XPS spectrum, typically located at 286.0–286.5 eV. This feature arises from ether‑like or alcohol‑like species, including polyethers and other organic decomposition products. It serves as a general indicator of the organic fraction of the CEI. Its intensity often increases with cycling. Alternative names: Ether/hydroxyl carbon peak, C–O component.
"""

XPS_NiF2_Peak = """
XPS NiF₂ Peak refers to the signals from nickel fluoride formed on the surface of Ni‑rich cathodes (e.g., NMC, NCA) due to side reactions involving HF attack. The Ni 2p₃/₂ peak appears at ~857.0 eV and the F 1s peak at ~684.0 eV. The presence and intensity of NiF₂ peaks indicate severe surface corrosion, transition metal dissolution, and electrolyte decomposition. Alternative names: Nickel fluoride peak, NiF₂ signal.
"""

LixPOyFz = """
LiₓPOᵧF₂ (Oxyfluorophosphate Species) are complex decomposition products of LiPF₆ salt, such as Li₂PO₃F, LiPF₂O₂, and Li₄PO₄F. They are detected in XPS in the F 1s region (~686–688 eV) and P 2p region (~134–137 eV). These species accumulate on the cathode surface during cycling and represent electrolyte salt degradation. Their relative abundance correlates with the degree of parasitic reactions. Alternative names: Phosphorus oxyfluorides, PF‑containing species.
"""


# ==================== Agent 2: Electrochemical Performance Parameters ====================

Electronic_Conductivity_Bulk = """
Bulk Electronic Conductivity is the ability of the cathode composite electrode (or pure cathode material pellet) to conduct electrons (or holes) under macroscopic conditions, expressed in S/cm. High electronic conductivity minimizes ohmic polarization and improves rate capability. For pure cathode materials, conductivity can vary from insulating (LiFePO₄) to metallic (LiCoO₂). For composite electrodes, conductive additives (carbon) enhance the overall conductivity. Alternative names: Electrical conductivity, Electronic transport.
"""

Initial_Coulombic_Efficiency = """
Initial Coulombic Efficiency (ICE) is the ratio of the first discharge (lithiation) capacity to the first charge (delithiation) capacity for a cathode material in a half‑cell (vs. Li⁺/Li), expressed in %. Typically, ICE < 100% because of irreversible processes such as solid‑electrolyte interphase (CEI) formation, structural irreversibility (e.g., oxygen loss in Li‑rich or Ni‑rich cathodes), or kinetic limitations. ICE = (1st discharge capacity / 1st charge capacity) × 100%. High ICE (>90%) is desirable for practical full‑cell energy density. Alternative names: First coulombic efficiency, First cycle efficiency.
"""

Discharge_Specific_Capacity_Initial = """
Initial Discharge Specific Capacity is the specific capacity delivered by the cathode during the first discharge (lithiation) after initial charge, expressed in mAh/g. This value is measured under a defined current density (e.g., 0.1C) and voltage window. It reflects the accessible reversible capacity after the first activation cycle and is a key performance metric for comparing materials. Alternative names: First discharge capacity, Initial specific discharge capacity, 首次放电比容量, 首圈放电比容量, 初始放电比容量.
"""

Rate_Performance = """
Rate Performance (Capacity Ratio) is the ratio of the discharge capacity measured at a high current rate (e.g., 5C, 10C, 20C) to that measured at a low reference rate (e.g., 0.2C or 0.1C), expressed as a dimensionless number (or percentage). A high ratio indicates good rate capability. For example, 85% at 5C means the material retains 85% of its low‑rate capacity. Alternative names: C‑rate capability, Rate retention.
"""

Capacity_Retention_Ratio = """
Capacity Retention Ratio is the percentage of the initial discharge capacity retained after a specified number of cycles (e.g., 100, 500, or 1000 cycles) at a given current density (e.g., 1C), expressed as “% @ N cycles”. It is a direct measure of cycle life and is influenced by structural stability, CEI evolution, particle cracking, and transition metal dissolution. For example, 85.2% @500 cycles. Alternative names: Capacity retention, Cycling stability, 容量保持率, 循环保持率.
"""

Rate_Capability_Profile = """
Rate Capability Profile is the set of discharge specific capacities (or capacity retention ratios) measured at progressively increasing current rates (e.g., 0.2C, 0.5C, 1C, 2C, 5C, 10C), often accompanied by the corresponding polarization voltages. It provides a comprehensive view of the material’s high‑rate performance and is typically reported as a list or table (e.g., “0.2C: 180 mAh/g, 1C: 170 mAh/g, 5C: 140 mAh/g”). Alternative names: Rate capability data, Multirate performance.
"""

Nominal_Discharge_Voltage = """
Nominal Discharge Voltage (or Plateau Voltage) is the voltage at which the cathode material delivers most of its capacity during discharge, often taken as the midpoint voltage (50% depth of discharge) or the voltage corresponding to the peak in the dQ/dV curve. It is expressed in V vs. Li⁺/Li. For example, LiFePO₄ has a flat plateau at ~3.45 V, while layered oxides show a sloping profile with an average around 3.7–3.8 V. This parameter directly influences the energy density of a full cell. Alternative names: Average discharge voltage (if integrated), Median voltage.
"""

Average_Discharge_Voltage = """
Average Discharge Voltage is the integrated mean voltage over the entire discharge process, calculated as (∫ V·dQ) / (∫ dQ), expressed in V vs. Li⁺/Li. It represents the energy‑weighted average potential and is the correct voltage to use when calculating energy density from capacity. For a perfectly flat plateau, it equals the nominal voltage. Alternative names: Mean discharge voltage, Energy‑average voltage.
"""

Charge_Discharge_Voltage_Gap = """
Charge‑Discharge Voltage Gap (Voltage Hysteresis) is the difference in voltage between the charge (delithiation) and discharge (lithiation) curves at the same state‑of‑charge (SOC), expressed in V. Hysteresis is caused by kinetic limitations, phase transformations, or structural rearrangements. A large voltage gap reduces energy efficiency. This parameter is often evaluated from constant‑current charge/discharge curves or from GITT equilibrium potential measurements. Alternative names: Voltage hysteresis, ΔV hysteresis.
"""

Ion_Diffusion_Coefficient = """
Lithium Ion Diffusion Coefficient (chemical diffusion coefficient, D_Li) is a kinetic parameter that quantifies the rate of lithium‑ion diffusion within the bulk of the cathode active material, expressed in cm²/s. It is typically measured by the galvanostatic intermittent titration technique (GITT) or by cyclic voltammetry (CV) at different scan rates, and is often strongly dependent on the state of charge. Higher D_Li values correlate with better rate capability. For typical cathodes, D_Li ranges from 10⁻⁸ to 10⁻¹² cm²/s. Alternative names: Li diffusivity, GITT diffusion coefficient, D_Li.
"""

Gravimetric_Energy_Density = """
Gravimetric Energy Density is the total electrical energy stored per unit mass of the full cell (or, in cathode research, often calculated based on the mass of active cathode material only, assuming an ideal anode), expressed in Wh/kg. For a cathode material, it is computed as (discharge specific capacity in mAh/g × average discharge voltage in V) / 3.6. For a full cell, it includes the masses of anode, electrolyte, separator, and current collectors. This is the primary metric for lightweight applications. Alternative names: Specific energy, Mass energy density.
"""

Volumetric_Energy_Density = """
Volumetric Energy Density is the total electrical energy stored per unit volume of the full cell (or the cathode electrode layer), expressed in Wh/L. For a cathode, it is estimated as (volumetric capacity in mAh/cm³ × average discharge voltage in V) / 3.6, where volumetric capacity is areal capacity (mAh/cm²) divided by electrode thickness (cm). This metric is critical for space‑constrained applications such as portable electronics and electric vehicles. Alternative names: Volume energy density, Energy density by volume.
"""

Charge_Transfer_Resistance = """
Charge Transfer Resistance (R_ct) is the kinetic resistance to the transfer of lithium ions across the electrode/electrolyte interface during the Faradaic reaction (Li⁺ + e⁻ ⇌ Li in the solid), expressed in Ω or Ω·cm². In electrochemical impedance spectroscopy (EIS), it appears as the diameter of the mid‑frequency semicircle in the Nyquist plot. A lower R_ct indicates faster interfacial kinetics. The surface film resistance (R_SEI or R_CEI) may also be present as a high‑frequency semicircle. Alternative names: Interfacial charge transfer resistance, R_ct.
"""

SEI_Resistance = """
SEI Resistance (R_SEI) is the ionic resistance of the passivation film on the cathode surface (written R_SEI in most papers, strictly R_CEI), extracted from the high-frequency semicircle in EIS or from distribution of relaxation times (DRT). It reflects the ability of the cathode surface film to conduct Li⁺ ions. A low R_SEI is desirable for high-rate capability. Alternative names: R_SEI, R_CEI, Surface film resistance, Cathode film resistance, SEI impedance, SEI膜电阻, SEI阻抗, 界面膜电阻.
"""

Self_Discharge_Rate = """
Self‑Discharge Rate is the rate of spontaneous capacity loss when a battery (or half‑cell) is stored at open circuit, expressed as % capacity loss per unit time (e.g., %/month or %/day). Self‑discharge in cathodes can arise from electrolyte oxidation, transition metal dissolution, or internal redox shuttles. A low self‑discharge rate (<5% per month) is required for practical batteries. Alternative names: Open‑circuit capacity loss, Storage loss.
"""

Thermal_Runaway_Onset_Temperature = """
Thermal Runaway Onset Temperature (T_on) is the critical temperature at which an exothermic side reaction in the cathode (or full cell) becomes self‑accelerating, leading to uncontrolled temperature rise, expressed in °C or K. For cathode materials, this is often associated with oxygen release and reaction with the electrolyte. Higher T_on indicates better thermal safety. It is measured by accelerating rate calorimetry (ARC) or differential scanning calorimetry (DSC) on charged electrodes. Alternative names: Decomposition temperature, Onset temperature for thermal runaway.
"""

Phase_Transition_Voltage = """
Phase Transition Voltage is the characteristic voltage at which a cathode material undergoes a crystallographic phase transformation during (de)lithiation, expressed in V vs. Li⁺/Li. For example, LiFePO₄ exhibits a two‑phase reaction at ~3.45 V; layered NMC shows multiple phase transitions (e.g., H1→M, M→H2, H2→H3) at distinct voltages. These voltages are typically identified as peaks in the dQ/dV curve or as plateaus in the voltage‑capacity profile. Alternative names: Phase change voltage, Transformation voltage.
"""

Transition_Metal_Dissolution_Amount = """
Transition Metal Dissolution Amount is the concentration of transition metal ions (e.g., Mn, Co, Ni, Fe) that leach from the cathode into the electrolyte during cycling or storage, expressed in ppm or μg/L. Dissolution is especially severe for Mn‑containing cathodes (e.g., LiMn₂O₄, NMC) and is promoted by HF attack. Dissolved metal ions deposit on the anode, disrupting the SEI and causing capacity fade. The amount is typically measured by ICP‑MS or ICP‑OES on aged electrolyte. Alternative names: Transition metal leaching, Cation dissolution.
"""

O2_CO2_Evolution = """
O₂ and CO₂ Evolution refers to the amount of oxygen and carbon dioxide gas released from the cathode during charge (especially at high voltages) or during thermal abuse, expressed in μmol/g or as relative intensity from online gas analysis. Oxygen evolution is a sign of lattice oxygen redox and structural degradation, particularly in Li‑rich and Ni‑rich layered oxides. CO₂ evolution typically indicates electrolyte oxidation. This parameter is measured by differential electrochemical mass spectrometry (DEMS) or gas chromatography (GC). Alternative names: Gas evolution, Oxygen release, CO₂ outgassing.
"""

# ==================== Agent 3: Preparation and Testing Conditions ====================

Active_Material_Mass_Fraction = """
Active Material Mass Fraction is the weight percentage of the electrochemically active cathode material (e.g., NMC, LFP) in the dry electrode coating, excluding the current collector. The remainder consists of conductive additive(s) and polymeric binder. Typical values range from 85% to 95% for practical electrodes. This fraction determines the effective capacity of the electrode per unit mass of coating. Alternative names: Active material loading (wt%), Cathode active content.
"""

Electrode_Thickness = """
Electrode Thickness is the physical thickness of the composite electrode coating on one side of the current collector, expressed in μm (typically 30–150 μm for cathodes). It does not include the current collector. Thickness, together with areal mass loading, determines the electrode density and influences ionic transport resistance. It is measured by a micrometer or by SEM cross‑section imaging. Alternative names: Coating thickness, Electrode coating thickness.
"""

Mass_Loading = """
Mass Loading (Areal Loading) is the mass of the dry electrode coating (active material + conductive additive + binder) per unit geometric area on one side of the current collector, expressed in mg/cm². For high‑energy cells, cathode mass loadings typically range from 10 to 30 mg/cm². Higher loadings increase energy density but may worsen rate capability due to longer ion transport paths. Alternative names: Areal mass loading, Coating density per area.
"""

Compacted_Density = """
Compacted Density (ρ) is the apparent density of the electrode coating after calendering (pressing), calculated as the mass loading divided by the electrode thickness (excluding current collector), expressed in g/cm³. Higher compacted density improves volumetric energy density but reduces porosity and may impede electrolyte wetting. Typical cathode compacted densities range from 2.0 to 3.5 g/cm³. Alternative names: Calendered density, Electrode density.
"""

Conductive_Additive_Binder_Ratio = """
Conductive Additive/Binder Ratio refers to the mass percentages of conductive carbon (e.g., Super P, carbon nanotubes) and polymer binder (e.g., PVDF, PTFE) in the electrode formulation, typically expressed as “conductive additive wt% / binder wt%”. The ratio affects the electronic conductivity, mechanical integrity, and porosity of the electrode. Alternative names: Carbon/binder ratio, Recipe composition.
"""

Electronic_Conductivity_Electrode = """
Electrode Electronic Conductivity is the effective electron conductivity of the composite cathode electrode (including active material, conductive additive, and binder), expressed in S/cm. It is typically higher than that of the pure active material due to the conductive network formed by carbon additives. It is measured by four‑point probe or two‑probe DC polarization on coated electrodes. Alternative names: Composite electrode conductivity, Effective electronic conductivity.
"""

Peel_Strength = """
Peel Strength (Adhesion Strength) is the force required to detach the electrode coating from the current collector per unit width, measured in N/m. It is typically determined by a 90° or 180° peel test using a universal testing machine. Adequate peel strength (>10 N/m for cathodes) is necessary to prevent delamination during battery assembly and cycling. Alternative names: Adhesion strength, Coating adhesion.
"""

Electrode_Porosity = """
Electrode Porosity (ε) is the volume fraction of voids within the dry electrode coating, expressed in %. It is calculated from the compacted density and the true density (measured by gas pycnometry) of the solid components. Typical cathode porosity values are 25–45%. Porosity affects electrolyte uptake, ionic conductivity, and mechanical stability. Alternative names: Coating porosity, Void fraction.
"""

Adhesion_Strength = """
Adhesion Strength is the force required to detach an electrode coating from its current collector, expressed in N/m (180° peel test) or MPa. It reflects coating quality, binder effectiveness, and calendering damage, and directly affects cycling stability. Alternative names: peel strength, bonding strength, adhesion force, coating adhesion.
"""

Mesoscopic_Porosity = """
Mesoscopic Porosity is the volume fraction of mesopores (2-50 nm) in an electrode or material, expressed in %. It governs electrolyte penetration, ion transport pathways, and rate capability. Measured by mercury intrusion porosimetry (MIP) or nitrogen adsorption. Alternative names: mesopore porosity, mesopore fraction, mesoporosity.
"""

