"""
Label definitions for lithium-ion battery anode materials and cell performance parameters.
"""

# ==================== Agent 1: Material Intrinsic Properties ====================

Elemental_Composition = """
Elemental Composition refers to the types of chemical elements present in the anode material, such as C, O, F, S, N, Li, Sn, Sb, Bi, Zn, Ge, P, B, Si, etc. It is typically expressed as a categorical string or list of element symbols. This parameter defines the fundamental chemistry of the anode and determines possible reaction mechanisms, alloying/dealloying behavior, or intercalation host properties. Alternative names: Chemical elements, Constituent elements.
"""

Chemical_Formula = """
Chemical Formula is the stoichiometric representation of the anode active material, e.g., Li₄.₄Si, Sn, LiC₆, Li₄.₄Ge, MoS₂, VS₂, etc. It is expressed as a string. The chemical formula defines the exact atomic ratios and is essential for calculating theoretical capacity, determining reaction products, and identifying phase purity. Alternative names: Stoichiometric formula, Composition formula.
"""

Crystal_System = """
Crystal System is the classification of the three-dimensional arrangement of atoms in the anode crystal lattice based on the symmetry and lengths/angles of the unit cell axes. The seven crystal systems are triclinic, monoclinic, orthorhombic, tetragonal, trigonal, hexagonal, and cubic. For anode materials (e.g., graphite: hexagonal; Li₂Ti₅O₁₂: cubic spinel; Sn: tetragonal), the crystal system influences lithium diffusion pathways, mechanical properties, and volume change anisotropy during lithiation/delithiation.
"""

Space_Group = """
Space Group (Crystal Space Group) is the complete set of symmetry operations (translations, rotations, mirrors, screw axes, and glide planes) that define the symmetry of the anode crystal structure. It is denoted by symbols such as P6₃/mmc (graphite), Fd-3m (Li₄Ti₅O₁₂), I4₁/amd (β-Sn), or P2/m. The space group governs the topology of lithium diffusion channels and determines phase transition pathways during electrochemical cycling. Alternative names: Crystallographic space group.
"""

Lattice_Parameters = """
Lattice Parameters are the geometric quantities defining the shape and size of the unit cell in the anode crystal structure, including the three edge lengths (a, b, c) and three interaxial angles (α, β, γ). Units are typically Å for lengths and degrees for angles. For anode materials such as graphite, silicon, tin, Li₄Ti₅O₁₂, or conversion-type oxides, lattice parameters change upon lithium insertion/extraction, reflecting volume expansion/contraction. Accurate lattice parameters are essential for understanding structural evolution, phase transformations, and mechanical stability.
"""

Crystallite_Size = """
Crystallite Size is the average diameter of coherently diffracting domains (nanocrystallites) within the anode active material, expressed in nm. It is commonly determined by X‑ray diffraction line broadening analysis using the Scherrer equation or by direct imaging via high‑resolution transmission electron microscopy (HR‑TEM). Smaller crystallites shorten solid‑state diffusion paths for lithium ions and accommodate volume changes more effectively but may increase side reactions due to larger surface area. Alternative names: Grain size (nanoscale), Coherent domain size.
"""

Interlayer_Spacing = """
Interlayer Spacing (d‑spacing) is the perpendicular distance between adjacent atomic layers in layered anode materials such as graphite, MoS₂, VS₂, TiS₂, or MXenes. It is expressed in Å or nm. For graphite, the (002) interlayer spacing is typically ~3.35 Å; expansion upon lithiation yields LiC₆ with increased spacing. This parameter directly affects lithium intercalation/deintercalation kinetics, reversible capacity, and structural stability. Alternative names: d‑spacing, Layer spacing.
"""

Unit_Cell_Volume_Change = """
Unit Cell Volume Change upon Lithiation is the percent change in the unit cell volume when the anode material transforms from its delithiated (or pristine) state to its fully lithiated state, expressed in %. It is a critical indicator of mechanical stability during cycling. Large volume changes (e.g., Si: ~300%, Sn: ~260%) cause particle pulverization, loss of electrical contact, and rapid capacity fading. Smaller volume changes (e.g., graphite: ~10%, Li₄Ti₅O₁₂: ~0.2%) promote long cycle life. This parameter is determined by in situ or ex situ XRD refinement. Alternative names: Volume expansion ratio, Lithiation-induced volume change.
"""

Band_Gap = """
Electronic Band Gap is the energy difference between the conduction band minimum (CBM) and the valence band maximum (VBM), expressed in eV. The band gap determines the intrinsic electronic conductivity of the anode material: zero or small band gap indicates metallic or semimetallic behavior (e.g., graphite, Sn, Si), whereas a large band gap indicates poor electronic conductivity (e.g., Li₄Ti₅O₁₂, TiO₂, conversion‑type oxides). For wide‑band‑gap anodes, conductive coatings or doping are required to enhance rate capability. Alternative names: Energy gap, Electronic band gap.
"""

Li_Ion_Migration_Barrier = """
Lithium Ion Migration Barrier is the minimum energy that a lithium ion must overcome to hop between adjacent stable sites within the anode crystal lattice, expressed in eV. It is typically obtained from density functional theory (DFT) calculations using the nudged elastic band (NEB) method. A lower migration barrier indicates faster lithium diffusion and better rate capability. For graphite, the barrier for in‑plane diffusion is low (~0.1–0.2 eV), while for Li₄Ti₅O₁₂, the barrier along the 3D channels is moderate (~0.3–0.5 eV). Alternative names: Li diffusion barrier, Activation energy for Li migration.
"""

Theoretical_Specific_Capacity = """
Theoretical Specific Capacity is the maximum charge per unit mass that the anode material can deliver when all active lithium ions (or all active redox centers) participate in the electrochemical reaction, based on Faraday’s law. Units: mAh/g. It is calculated as Q_th = (n × F) / (3.6 × M), where n is the number of electrons transferred per formula unit, F is the Faraday constant (96 485 C/mol), and M is the molar mass. For alloying anodes: Si (n=4.4) → 4200 mAh/g, Sn (n=4.4) → 994 mAh/g; for intercalation anodes: graphite (LiC₆) → 372 mAh/g; for conversion anodes: Fe₂O₃ → 1007 mAh/g. Alternative names: Theoretical capacity, Maximum specific capacity.
"""

LiF_Content_in_SEI_XPS = """
LiF Content in SEI XPS is the relative amount of lithium fluoride (LiF) present in the solid‑electrolyte interphase (SEI) on the anode surface, quantified by X‑ray photoelectron spectroscopy (XPS) analysis of the F 1s peak. It is typically expressed as atomic percent (at%) or as a peak area ratio. LiF is a key inorganic SEI component that provides mechanical robustness and electronic insulation while allowing lithium‑ion conduction. Moderate LiF content improves SEI stability, but excessive LiF may increase interfacial resistance. Alternative names: LiF fraction, Fluoride content in SEI.
"""

SEI_Chemical_Composition_XPS = """
SEI Chemical Composition XPS refers to the identification and quantification of organic and inorganic species in the solid‑electrolyte interphase (SEI) on the anode surface, derived from high‑resolution spectra (C 1s, Li 1s, F 1s, P 2p, O 1s, etc.). Typical components include LiF, Li₂CO₃, LiₓPFᵧ, ROCO₂Li (lithium alkyl carbonates), RO‑Li (lithium alkoxides), and polymeric species. Each component is identified by its characteristic binding energy (eV). The SEI composition determines the interphase stability, ionic conductivity, and passivation effectiveness. Alternative names: SEI speciation, Interphase chemical makeup.
"""

Exchange_Current_Density = """
Exchange Current Density (i₀) is the current density at the electrode/electrolyte interface when the electrode reaction is at equilibrium (i.e., net current = 0), expressed in mA/cm² or A/cm². It is a kinetic parameter that reflects the intrinsic charge‑transfer rate of the anode reaction (e.g., Li⁺ + e⁻ ⇌ Li). A higher i₀ indicates faster interfacial kinetics, lower activation overpotential, and better rate capability. It is determined from Tafel plots or by fitting the Butler‑Volmer equation to experimental polarization data. Alternative names: Intrinsic exchange current density.
"""

# ==================== Agent 2: Electrochemical Performance Parameters ====================

Initial_Coulombic_Efficiency = """
Initial Coulombic Efficiency (ICE) is the ratio of the first charge (delithiation) capacity to the first discharge (lithiation) capacity, expressed in %. For anodes, ICE = (1st delithiation capacity) / (1st lithiation capacity) × 100%. ICE values below 100% indicate irreversible lithium consumption due to SEI formation, trapped lithium in the host structure, or incomplete conversion reactions. A high ICE (e.g., graphite >90%, Li₄Ti₅O₁₂ ~99%) is desirable for full‑cell energy density. Alternative names: First cycle efficiency, First coulombic efficiency, 首效, 首次库仑效率, 初始库仑效率.
"""

First_Lithiation_Capacity = """
First Lithiation Capacity is the specific capacity delivered during the first discharge (lithiation) of the anode, typically expressed in mAh/g. This value includes both reversible and irreversible contributions. It is measured under a defined current density (e.g., 0.05 C or 0.1 C) and voltage window. The first lithiation capacity reflects the initial lithium storage capability and is often higher than subsequent cycles due to SEI formation or activation processes. Alternative names: First discharge capacity, Initial lithiation capacity, 首圈嵌锂容量, 首次嵌锂容量.
"""

Reversible_Capacity_First_Cycle = """
Reversible Capacity (First Cycle) is the specific capacity recovered upon first delithiation (charge) after the initial lithiation, expressed in mAh/g. It equals the first lithiation capacity minus the first-cycle irreversible capacity loss (Q_delithiation(1st) = Q_lithiation(1st) - IRL), and represents the practically usable capacity of the anode in the first cycle. Alternative names: First-cycle reversible capacity, First delithiation capacity, Reversible specific capacity, 可逆比容量, 首圈可逆容量, 首次脱锂容量.
"""

Pseudocapacitive_Contribution_Ratio = """
Pseudocapacitive Contribution Ratio is the fraction of the total charge storage that originates from surface or near‑surface fast redox reactions (pseudocapacitance) rather than diffusion‑limited bulk intercalation/alloying/conversion. It is expressed as a percentage (%). This ratio is determined from cyclic voltammetry at multiple scan rates using the power‑law relationship (i = avᵇ) and the Dunn method. A higher pseudocapacitive contribution indicates faster kinetics and better rate capability, especially for nanostructured or conversion‑type anodes. Alternative names: Capacitive contribution, Surface‑controlled contribution.
"""

Rate_Capability_at_Given_C_rate = """
Rate Capability at Given C‑rate is the specific discharge (lithiation) capacity delivered by the anode at a specified high current rate (e.g., 5 C, 10 C, 20 C), expressed in mAh/g. It reflects the ability of the anode material and electrode architecture to sustain fast charge/discharge. Typically reported alongside the capacity at a low reference rate (e.g., 0.1 C or 0.2 C) for comparison. Alternative names: High‑rate capacity, C‑rate performance.
"""

Energy_Density_Full_Cell = """
Energy Density (Full Cell) is the total electrical energy stored per unit mass (gravimetric, Wh/kg) or per unit volume (volumetric, Wh/L) of a complete lithium‑ion battery cell. It depends on the specific capacities and operating voltages of both the cathode and anode, as well as the mass fraction of active materials, electrolyte, separator, and current collectors. For anode research, full‑cell energy density is the ultimate practical metric. Alternative names: Cell‑level energy density, Practical energy density.
"""

Critical_Current_Density_for_Li_Dendrite_Onset = """
Critical Current Density for Li Dendrite Onset is the current density (expressed in mA/cm² or A/g) beyond which lithium dendrites begin to form on the lithium metal anode during plating. It is determined by observing a sudden voltage drop or plateau in galvanostatic polarization curves, or via in situ optical microscopy. A higher critical current density indicates better suppression of dendrite growth, which is essential for safe lithium‑metal batteries. Alternative names: Dendrite initiation current density, Threshold current density.
"""

Volumetric_Capacity = """
Volumetric Capacity is the amount of charge stored per unit volume of the anode electrode (including active material, conductive additive, binder, and pores), expressed in mAh/cm³. It is calculated as the areal capacity (mAh/cm²) divided by the electrode thickness (cm). Volumetric capacity is a critical metric for space‑constrained applications such as portable electronics and electric vehicles. Alternative names: Volume‑based capacity, Capacity per unit volume.
"""

Areal_Capacity = """
Areal Capacity is the amount of charge stored per unit geometric area of the anode electrode, expressed in mAh/cm². It is directly measured from galvanostatic charge/discharge testing and depends on the mass loading of active material and its specific capacity. High areal capacity (>3 mAh/cm² for anodes) is required for practical high‑energy‑density cells. Alternative names: Capacity per unit area, Area‑specific capacity.
"""

Average_Operating_Voltage_vs_Li_Li = """
Average Operating Voltage (vs. Li/Li⁺) is the mean potential of the anode during lithiation or delithiation relative to the Li⁺/Li reference electrode, expressed in V. It is calculated by integrating the voltage vs. capacity curve and dividing by the total capacity, or taken as the midpoint voltage of the plateau. For anodes, lower average lithiation voltages (e.g., graphite ~0.1 V, Si ~0.2–0.4 V) enable higher full‑cell voltages and energy densities but increase the risk of lithium plating. Alternative names: Mean discharge voltage, Average lithiation potential.
"""

Cycle_Life_80_Percent_Capacity_Retention = """
Cycle Life (80% Capacity Retention) is the number of charge/discharge cycles that a full cell or anode can undergo before its discharge capacity decays to 80% of its initial value. It is expressed as an integer number of cycles. This metric is the primary indicator of long‑term durability and is influenced by structural stability of the anode material, SEI growth, volume change management, and electrolyte decomposition. Alternative names: Cycle life to 80% SOH, Service life.
"""

Rate_Recovery_Percent = """
Rate Recovery (expressed as %) is the ratio of the discharge capacity measured at a low reference rate (e.g., 0.1 C or 0.2 C) after completing a series of high‑rate tests, to the initial low‑rate capacity. It quantifies the reversibility of rate‑induced degradation (e.g., mechanical damage, SEI fracture, or lithium trapping). A rate recovery close to 100% indicates excellent structural resilience and stable electrochemistry. Alternative names: Capacity recovery, Rate reversibility.
"""

Symmetric_Cell_Cycling_Stability = """
Symmetric Cell Cycling Stability refers to the performance of a symmetric cell (e.g., Li||Li, LiₓSi||LiₓSi, or LiₓSn||LiₓSn) under galvanostatic cycling, reported as the stable cycling duration (in hours) and the corresponding overpotential (in mV). This parameter is used to evaluate the long‑term stability and reversibility of anode materials or artificial SEI layers without the influence of a counter‑electrode variation. A longer stable cycling time and lower overpotential indicate better electrode stability. Alternative names: Li symmetric cell performance, Symmetric cell longevity.
"""

Average_Coulombic_Efficiency_Stable_Cycling = """
Average Coulombic Efficiency (Stable Cycling) is the mean value of coulombic efficiency calculated over the stable cycling period (excluding the first few formation cycles), expressed in %. For anodes, values >99.5% are typically required for practical long‑life batteries. This parameter reflects the degree of parasitic reactions and lithium inventory loss per cycle. Alternative names: Mean CE, Stable cycling CE.
"""

Capacity_Retention_at_Nth_Cycle = """
Capacity Retention at Nth Cycle is the percentage of the initial discharge capacity retained after a specified number of cycles (e.g., 100, 500, or 1000 cycles), expressed as “% @ N cycles”. It is a direct measure of cycle life under given test conditions (current density, voltage window, temperature). For anodes, capacity retention is influenced by volume change, SEI stability, and material fracture. Alternative names: Capacity retention ratio, Cyclability, 循环保持率, 容量保持率, N圈容量保持率.
"""

Open_Circuit_Voltage = """
Open‑Circuit Voltage (OCV) is the equilibrium potential difference between the positive and negative electrodes of a battery when no external current is flowing, expressed in V. For a freshly assembled cell, OCV reflects the chemical potential difference between the cathode and anode materials. After partial cycling, OCV relaxation can indicate state of charge or self‑discharge. Alternative names: Resting voltage, Equilibrium voltage.
"""

Surface_Diffusion_Controlled_Contribution = """
Surface‑/Diffusion‑Controlled Contribution (at a given scan rate v) is the fraction of total charge storage that is attributed to surface‑limited (capacitive or pseudocapacitive) processes versus diffusion‑limited intercalation/conversion processes, expressed in %. It is determined by analyzing cyclic voltammetry data at multiple scan rates using the equation i = k₁v + k₂v¹/², where k₁v represents the surface‑controlled current and k₂v¹/² the diffusion‑controlled current. A higher surface‑controlled contribution at high scan rates indicates good rate capability. Alternative names: Capacitive contribution ratio, k₁/k₂ analysis.
"""

Irreversible_Capacity_Loss_First_Cycle = """
Irreversible Capacity Loss (First Cycle) is the difference between the first lithiation capacity and the first delithiation capacity of the anode, expressed in mAh/g. It corresponds to the amount of lithium consumed irreversibly, primarily due to SEI formation, trapped lithium in alloying anodes, or incomplete conversion reactions. IRL = Q_lithiation(1st) – Q_delithiation(1st). Alternative names: First‑cycle irreversible capacity, ICL (irreversible capacity loss).
"""

Ionic_Conductivity_of_SEI = """
Ionic Conductivity of SEI is the ability of the solid‑electrolyte interphase (or artificial SEI layer) to conduct lithium ions, expressed in S/cm. It is a critical parameter for battery rate performance and low‑temperature operation. Ideally, an SEI should have high Li⁺ conductivity (≥10⁻⁵ S/cm) and negligible electronic conductivity. This value is typically measured by electrochemical impedance spectroscopy (EIS) using symmetric cells or by DC polarization methods. Alternative names: Li⁺ conductivity of SEI, SEI ionic conductivity.
"""

SEI_Resistance = """
SEI Resistance (R_SEI) is the ionic resistance of the solid-electrolyte interphase on the anode surface, extracted from the high-frequency semicircle in EIS or from distribution of relaxation times (DRT). It reflects the resistance of the passivation film to lithium-ion transport; an increase upon cycling indicates SEI growth or degradation. Alternative names: R_SEI, Rsei, R_sei, Surface film resistance, SEI impedance, SEI膜电阻, SEI阻抗, 界面膜电阻.
"""

Charge_Transfer_Resistance = """
Charge Transfer Resistance (R_ct) is the kinetic resistance to the Faradaic reaction at the electrode/electrolyte interface, expressed in Ω (or Ω·cm² when area-normalized). It appears as the mid-frequency semicircle in the Nyquist plot; a lower R_ct indicates faster interfacial kinetics. Alternative names: R_ct, Rct, RCT, charge transfer resistance, 电荷转移电阻, 界面电荷转移电阻.
"""

Chemical_Diffusion_Coefficient_of_Li_GITT = """
Chemical Diffusion Coefficient of Lithium (D_Li) is a kinetic parameter quantifying the rate of lithium‑ion diffusion within the bulk of the anode active material, expressed in cm²/s. It is typically measured by the galvanostatic intermittent titration technique (GITT) as a function of state of charge. The diffusion coefficient is state‑dependent and can vary over orders of magnitude during lithiation/delithiation. Higher D_Li values correlate with better rate capability. For example, graphite: 10⁻⁸–10⁻¹⁰ cm²/s; Li₄Ti₅O₁₂: 10⁻¹⁰–10⁻¹² cm²/s. Alternative names: Li diffusivity, GITT diffusion coefficient.
"""

Li_Dendrite_Nucleation_Overpotential = """
Li Dendrite Nucleation Overpotential is the extra potential below 0 V (vs. Li/Li⁺) required to initiate the deposition of lithium metal onto the anode surface, expressed in mV. It is observed as a voltage dip (undershoot) in the galvanostatic plating curve before reaching a steady‑state plateau. A higher nucleation overpotential indicates a higher energy barrier for dendrite formation, often associated with a more uniform SEI or artificial coating that promotes homogeneous nucleation. Alternative names: Nucleation overpotential, Dendrite initiation overpotential.
"""

Li_Dendrite_Growth_Rate = """
Li Dendrite Growth Rate is the velocity at which lithium dendrites elongate during plating, expressed in μm/min. It is measured by in situ optical microscopy or computed from ex situ post‑mortem analysis. A lower dendrite growth rate indicates better suppression of dendritic morphology, which is critical for safe lithium‑metal battery operation. The growth rate depends on current density, temperature, electrolyte composition, and SEI properties. Alternative names: Dendrite propagation rate.
"""

Activation_Energy_for_Li_Transport_through_SEI = """
Activation Energy for Li⁺ Transport through SEI (E_a,SEI) is the energy barrier that lithium ions must overcome to pass through the solid‑electrolyte interphase, expressed in kJ/mol or eV. It is determined by measuring the temperature dependence of the SEI‑related resistance using electrochemical impedance spectroscopy (EIS) and fitting to the Arrhenius equation. A lower activation energy indicates a more conductive SEI and better low‑temperature performance. Alternative names: E_a(SEI), SEI ionic conduction activation energy.
"""

Activation_Energy_for_Li_Desolvation = """
Activation Energy for Li⁺ Desolvation (E_a,desolv) is the energy barrier required for a lithium ion to shed its solvent coordination shell (e.g., EC, DMC, DEC) when approaching the electrode surface and entering the SEI or the anode lattice, expressed in kJ/mol or eV. It is derived from temperature‑dependent EIS measurements, typically from the charge‑transfer resistance (R_ct). A lower desolvation energy improves rate capability, especially at low temperatures. Alternative names: Desolvation energy barrier, E_a(desolvation).
"""

Li_Nucleation_Overpotential = """
Li Nucleation Overpotential is the extra overpotential (in mV) required to overcome the energy barrier for the formation of stable lithium nuclei on the anode substrate during the initial stage of electrodeposition. It is measured from the voltage difference between the sharp dip (minimum) in the galvanostatic plating curve and the subsequent steady‑state plateau. A higher nucleation overpotential is generally associated with smaller and more uniform lithium nuclei, which can lead to denser, dendrite‑free deposits. Alternative names: Nucleation barrier, Deposition overpotential (initial).
"""

Plateau_Overpotential = """
Plateau Overpotential (also known as steady‑state overpotential) is the constant overpotential observed during the stable growth stage of lithium metal deposition after the initial nucleation dip, expressed in mV (vs. Li/Li⁺). It reflects the combined contributions of ohmic drop, concentration polarization, and charge‑transfer resistance under steady‑state conditions. A low plateau overpotential indicates low overall polarization and efficient lithium plating/stripping. Alternative names: Steady‑state overpotential, Growth overpotential.
"""

# ==================== Agent 3: Preparation and Testing Conditions ====================

Electrode_Porosity = """
Electrode Porosity (ε) is the volume fraction of voids within the electrode coating (including active material, conductive additive, and binder) relative to the total electrode volume, expressed in %. It is typically measured by N₂ physisorption (BET) for surface area analysis, or by mercury intrusion porosimetry, or calculated from the true density of components and the apparent electrode density. Porosity affects electrolyte uptake, ionic transport, and mechanical integrity. Typical values: anodes 25–45%, cathodes 25–40%. Alternative names: Void fraction, Pore volume fraction.
"""

Coating_Thickness = """
Coating Thickness is the physical thickness of a surface layer applied onto the anode active material (e.g., carbon coating on Si, Al₂O₃ coating on graphite) or a polymer artificial SEI layer on lithium metal, expressed in nm or μm. For porous anodes, it can also refer to the thickness of the carbon or metal coating on nanostructured particles. This parameter is measured by transmission electron microscopy (TEM) or scanning electron microscopy (SEM) cross‑sectional imaging. The coating thickness significantly influences electronic conductivity, interfacial stability, and lithium‑ion transport resistance. Alternative names: Shell thickness, Surface layer thickness.
"""

Electrode_Compaction_Density = """
Electrode Compaction Density (ρ) is the apparent density of the electrode coating after calendering (excluding the current collector), expressed in g/cm³. It is calculated as the mass of the dry coating divided by its geometric volume (thickness × area). For anodes, typical compaction densities are 1.2–1.8 g/cm³ for graphite, and lower for silicon‑based anodes. Higher compaction density improves volumetric energy density but may reduce porosity and rate capability. Alternative names: Calendered density, Tapped density (for powders, not same).
"""

Artificial_SEI_Thickness = """
Artificial SEI Thickness is the physical thickness of a deliberately applied protective layer (polymer, ceramic, or composite) on the surface of a lithium metal anode (or other anode materials), expressed in μm. It is measured by cross‑sectional SEM or TEM. An optimal artificial SEI should be thin enough to allow fast Li⁺ transport but thick and robust enough to suppress dendrite penetration and mitigate side reactions. Alternative names: Protective layer thickness, Engineered interphase thickness.
"""

Youngs_Modulus = """
Young’s Modulus (E) is a measure of the stiffness or elastic modulus of a material, defined as the ratio of tensile stress to tensile strain within the elastic limit, expressed in GPa or MPa. For artificial SEI layers or binder materials used in anodes, Young’s modulus indicates mechanical robustness against volume changes and dendrite penetration. A high modulus (e.g., >1 GPa for polymer SEIs, >10 GPa for ceramic coatings) helps suppress dendrite growth. Alternative names: Elastic modulus, Tensile modulus.
"""

Tensile_Strength = """
Tensile Strength (σ) is the maximum tensile stress that a material can withstand before fracture, expressed in MPa. For artificial SEI layers, polymer binders, or free‑standing anode films, tensile strength reflects the material’s resistance to cracking during electrode fabrication and battery cycling. It is measured by stress‑strain testing (e.g., dog‑bone specimens). Alternative names: Ultimate tensile strength (UTS), Breaking strength.
"""

Elongation_at_Break = """
Elongation at Break (δ) is the percentage increase in length of a material at the point of fracture under tensile testing, expressed in %. For flexible artificial SEI layers or polymer binders, a higher elongation at break indicates better ductility and ability to accommodate electrode volume changes without cracking. Typical values range from <5% (brittle ceramics) to >200% (elastomers). Alternative names: Strain at break, Failure strain.
"""

Electrolyte_Wettability_Contact_Angle = """
Electrolyte Wettability is the ability of a liquid electrolyte to spread over the electrode surface, quantified by the contact angle (θ) measured in degrees (°) using the sessile drop method. A low contact angle (<30°) indicates good wettability, ensuring uniform electrolyte penetration into the porous electrode and low interfacial resistance. Poor wettability (θ > 90°) leads to insufficient electrolyte contact, increased polarization, and poor cycling performance. Alternative names: Contact angle, Wetting angle.
"""


Adhesion_Strength = """
Adhesion Strength is the force required to detach an electrode coating from its current collector, expressed in N/m (180° peel test) or MPa. It reflects coating quality, binder effectiveness, and calendering damage, and directly affects cycling stability. Alternative names: peel strength, bonding strength, adhesion force, coating adhesion.
"""

Mesoscopic_Porosity = """
Mesoscopic Porosity is the volume fraction of mesopores (2-50 nm) in an electrode or material, expressed in %. It governs electrolyte penetration, ion transport pathways, and rate capability. Measured by mercury intrusion porosimetry (MIP) or nitrogen adsorption. Alternative names: mesopore porosity, mesopore fraction, mesoporosity.
"""
