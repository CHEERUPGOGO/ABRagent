# -*- coding: utf-8 -*-
"""
Anode Material Extraction Rules

Each tag structure: Definition (including physical meaning / measurement method) + [Exclusion criteria] + [Extraction requirements].
Component name/type is handled by the pipeline's material_id and is not duplicated within individual tags.
"""

# ==================== Agent 1: Intrinsic Material Properties ====================

Elemental_Composition = """
Definition: The list of constituent elements present in the anode material.
Extraction requirements:
- Output as a list of strings, e.g., ["Si", "C", "O"].
- If the literature reports both elements and their contents, extract only the elements (content belongs to other tags).
- Ignore trace impurities below 0.1 at%.
- Order: descending by content; if content information is unavailable, sort alphabetically by element symbol.
"""

Crystal_System = """
Definition: The crystal system, selected from the seven standard Bravais lattice types.
Extraction requirements:
- Only the following values are allowed: cubic, hexagonal, tetragonal, trigonal, orthorhombic, monoclinic, triclinic.
- If the literature provides the space group but not the crystal system, derive the system automatically using the standard space group–crystal system mapping table.
- Do not extract for amorphous materials.
"""

Space_Group = """
Definition: The space group symbol in Hermann–Mauguin notation.
Extraction requirements:
- Preserve the standard representation; ASCII forms (e.g., P6_3/mmc) are acceptable.
- If multiple space groups are reported (e.g., different phases), record each phase as a separate object.
"""

Lattice_Parameters = """
Definition: The three lattice constants (a, b, c, in Å) and three interaxial angles (α, β, γ, in degrees) of a unit cell. This is the most fundamental geometric description of a crystal structure, used to calculate unit cell volume, atomic coordinates, and lattice strain during lithiation/delithiation.
Extraction requirements:
- Output as an object containing a, b, c, alpha, beta, gamma, all as floats.
- If only partial parameters are given (e.g., only a and c for a hexagonal system), set missing fields to 0.0 and note.
- Units: lattice constants uniformly in Å (1 nm = 10 Å), angles uniformly in degrees.
- Must specify the crystal system to correctly interpret the lattice parameters (e.g., cubic: a = b = c, α = β = γ = 90°).
- State: pristine, lithiated, delithiated, etc.
- Method: XRD Rietveld refinement, single-crystal XRD, neutron diffraction.
- If both calculated and experimental values are reported, extract as two separate objects.
- Alternative names commonly found in the literature: "lattice constants", "unit cell parameters", "cell dimensions", "lattice parameters a, b, c", "cell parameters", "crystallographic parameters".
"""

Crystallite_Size = """
Definition: The average crystallite diameter.
Extraction requirements:
- Unit uniformly in nm. If reported in μm, multiply by 1000.
- If given as a range (e.g., 10–20 nm), record the range.
- Must specify the measurement method (Scherrer / TEM).
"""

Interlayer_Spacing = """
Definition: The interlayer distance, typically referring to specific crystallographic planes such as (002).
Extraction requirements:
- Unit uniformly in Å. 1 nm = 10 Å.
- Must specify the corresponding crystallographic plane (e.g., "002") and material state (pristine / lithiated).
- Do not extract if the plane is not specified.
"""

Unit_Cell_Volume_Change = """
Definition: The percentage change in unit cell volume upon lithiation.
Extraction requirements:
- Unit in %. Positive values indicate expansion; negative values indicate contraction.
- Must explicitly state from_state and to_state.
- Do not extract if the literature only reports a-axis and c-axis changes without calculating the volume change (leave for post-processing calculation).
"""

Band_Gap = """
Definition: The electronic band gap.
Extraction requirements:
- Unit in eV.
- Distinguish between experimental and computed values (via the method field).
- Distinguish between direct and indirect band gaps (via the type field).
- For computed values, specify the functional (e.g., PBE, HSE06).
"""

Li_Ion_Migration_Barrier = """
Definition: The energy barrier for lithium ion migration.
Extraction requirements:
- Unit in eV. If given in kJ/mol, divide by 96.485 for conversion.
- Must specify the migration pathway (e.g., intra-layer, inter-layer, along c-axis).
- Restricted to DFT-calculated values (NEB, AIMD). Experimental activation energies belong under other tags.
"""

Theoretical_Specific_Capacity = """
Definition: The theoretical specific capacity.
Extraction requirements:
- Unit in mAh/g.
- Must be based on Faraday's law, with n (number of electrons transferred) and the corresponding reaction explicitly provided.
- Do not extract if only a numerical value is given without the calculation basis.
"""

LiF_Content_in_SEI_XPS = """
Definition: The LiF content in the SEI measured by XPS.
Extraction requirements:
- Unit uniformly in at%. If the literature reports area ratios, specify the unit as "area%".
- Must specify the cycle number or state (e.g., "after_50_cycles").
- If values at multiple states are reported, extract each separately.
"""

SEI_Chemical_Composition_XPS = """
Definition: The chemical species detected in the SEI.
Extraction requirements:
- Output as a list of strings using standard nomenclature: LiF, Li₂CO₃, ROCO₂Li, LiOH, Li₂O, LiₓPOyFz, RO⁻, etc.
- Extract only species with unambiguous assignments; exclude speculative descriptions such as "likely" or "possibly".
"""

Exchange_Current_Density = """
Definition: The exchange current density i₀.
Extraction requirements:
- Unit uniformly in mA/cm². If given in A/cm², multiply by 1000.
- Must specify the test temperature.
"""

# ==================== Agent 2: Electrochemical Performance ====================

Initial_Coulombic_Efficiency = """
Definition: The first-cycle Coulombic efficiency.
Extraction requirements:
- Unit in %. If given as a decimal (e.g., 0.85), convert to 85%.
- Must be linked to a condition_id.
- Do not extract the average CE over subsequent stable cycles.
"""

First_Lithiation_Capacity = """
Definition: The first lithiation (discharge) capacity.
Extraction requirements:
- Unit in mAh/g, based on active material mass.
- If based on total electrode mass, the mass loading must be noted in the condition; this tag only extracts the raw value.
- Distinguish between half-cell and full-cell configurations (via electrode_config).
"""

Reversible_Capacity_First_Cycle = """
Definition: The reversible specific capacity recovered upon first delithiation (charge) after the first lithiation.
Extraction requirements:
- Unit in mAh/g, based on active material mass.
- Do NOT map "可逆比容量"/reversible capacity to First_Lithiation_Capacity: First_Lithiation_Capacity includes irreversible contributions; use this tag for the reversible (delithiation) value.
- Must specify the cycle number if it refers to a later cycle than the first.
"""

Pseudocapacitive_Contribution_Ratio = """
Definition: The pseudocapacitive contribution ratio (%).
Extraction requirements:
- Unit in %.
- Must specify the scan rate (mV/s).
"""

Rate_Capability_at_Given_C_rate = """
Definition: The discharge capacity at multiple C-rates.
Extraction requirements:
- Output as a dictionary, with keys representing C-rate strings (e.g., "0.2C", "1C") and values representing capacities (mAh/g).
- If the rate is expressed as current density (e.g., 100 mA/g), attempt conversion to C-rate (requires theoretical capacity); if conversion is not possible, retain the original unit and note (e.g., "100 mA/g").
- A single material may have multiple sets of rate tests, each corresponding to a different condition_id.
"""

Critical_Current_Density_Dendrite = """
Definition: The critical current density for lithium dendrite growth.
Extraction requirements:
- Unit preferentially in mA/cm². If given in A/g, conversion requires knowledge of the mass loading; otherwise, retain the original unit and note.
- Must specify the electrolyte composition and substrate.
"""

Volumetric_Capacity = """
Definition: The volumetric specific capacity.
Extraction requirements:
- Unit in mAh/cm³.
"""

Areal_Capacity = """
Definition: The areal specific capacity.
Extraction requirements:
- Unit in mAh/cm².
"""

Average_Operating_Voltage = """
Definition: The average discharge voltage.
Extraction requirements:
- Unit in V, relative to the specified reference electrode (default: Li⁺/Li).
- Must specify whether the value is the integrated average or the median voltage.
"""

Cycle_Life_80_Retention = """
Definition: The number of cycles until capacity decays to 80% of its initial value.
Extraction requirements:
- Unit in cycles.
- If the retention cutoff is not 80% (e.g., 70%), specify the retention_cutoff value.
"""

Rate_Recovery = """
Definition: The capacity recovery percentage after transitioning from a high rate back to a low rate.
Extraction requirements:
- Unit in %.
- Must specify the high rate and low rate (e.g., "5C" / "0.2C").
"""

Symmetric_Cell_Stability = """
Definition: The stable cycling time and overpotential of a Li||Li symmetric cell.
Extraction requirements:
- Output as {"time_h": value, "overpotential_mV": value}.
- Must specify the current density and areal capacity.
"""

Average_Coulombic_Efficiency_Stable = """
Definition: The average Coulombic efficiency during the stable cycling regime (excluding the first cycle).
Extraction requirements:
- Unit in %.
- Must specify the cycle interval (e.g., [2, 200]).
"""

Capacity_Retention_at_Nth_Cycle = """
Definition: The capacity retention after a specified number of cycles.
Extraction requirements:
- Unit in %.
- Must specify the cycle number and test conditions (via condition_id).
"""

Open_Circuit_Voltage = """
Definition: The open-circuit voltage (OCV).
Extraction requirements:
- Unit in V.
- Must specify the SOC and resting time.
"""

Surface_Controlled_Contribution = """
Definition: The surface-controlled (pseudocapacitive) contribution ratio.
Extraction requirements:
- Unit in %.
- Must specify the scan rate (mV/s).
"""

Irreversible_Capacity_Loss_First = """
Definition: The irreversible capacity loss during the first cycle.
Extraction requirements:
- Unit in mAh/g.
"""

SEI_Ionic_Conductivity = """
Definition: The ionic conductivity of the SEI layer.
Extraction requirements:
- Unit in S/cm.
- Must specify the test temperature and method.
"""

SEI_Resistance = """
Definition: The ionic resistance of the solid-electrolyte interphase (SEI) on the anode, extracted from the high-frequency semicircle in EIS or DRT.
Extraction requirements:
- Unit in Ω or Ω·cm².
- Must specify the cycle number and SOC.
"""

Charge_Transfer_Resistance = """
Definition: The charge transfer resistance R_ct of the Faradaic reaction at the electrode/electrolyte interface, extracted from the mid-frequency semicircle in EIS.
Extraction requirements:
- Unit in Ω or Ω·cm².
- Must specify the cycle number and SOC.
"""

Chemical_Diffusion_Coefficient_GITT = """
Definition: The chemical diffusion coefficient of lithium ions measured by GITT.
Extraction requirements:
- Unit in cm²/s, using scientific notation (e.g., 2.3 × 10⁻¹⁰).
- Must specify the voltage point and temperature.
"""

Li_Dendrite_Nucleation_Overpotential = """
Definition: The nucleation overpotential for lithium dendrite formation.
Extraction requirements:
- Unit in mV (positive value).
- Must specify the current density and substrate.
"""

Li_Dendrite_Growth_Rate = """
Definition: The growth rate of lithium dendrites.
Extraction requirements:
- Unit in μm/min.
- Must specify the current density and observation method.
"""

Activation_Energy_SEI_Transport = """
Definition: The activation energy for Li⁺ transport through the SEI.
Extraction requirements:
- Unit in kJ/mol. If given in eV, multiply by 96.485 for conversion.
- Must specify the temperature range.
"""

Activation_Energy_Desolvation = """
Definition: The desolvation activation energy.
Extraction requirements:
- Unit in kJ/mol. If given in eV, multiply by 96.485 for conversion.
- Must specify the temperature range.
"""

Li_Nucleation_Overpotential = """
Definition: The nucleation overpotential for lithium metal deposition.
Extraction requirements:
- Unit in mV.
- Must specify the current density.
"""

Plateau_Overpotential = """
Definition: The steady-state overpotential during lithium metal deposition.
Extraction requirements:
- Unit in mV.
- Must specify the current density and areal capacity.
"""

# ==================== Agent 3: Electrode Preparation Parameters ====================

Electrode_Porosity = """
Definition: The volume fraction of void space in the electrode coating (%), measured by BET nitrogen adsorption, mercury intrusion porosimetry (MIP), or derived from true density and apparent density.
Extraction requirements:
- Unit uniformly in %.
- Must specify the measurement method (BET / MIP / calculation).
- If given as a range, use the midpoint value.
"""

Coating_Thickness = """
Definition: The thickness of a coating applied to the active material surface (e.g., carbon coating on silicon) or an artificial SEI layer, expressed in nm (carbon layer) or μm (polymer layer).
Extraction requirements:
- Unit: nm or μm; preferentially convert to nm (1 μm = 1000 nm).
- Must specify the measurement method (TEM / SEM).
- Distinguish between carbon coating and artificial SEI.
"""

Electrode_Compaction_Density = """
Definition: The compaction density of the calendered electrode coating, in g/cm³, calculated as the coating mass divided by the geometric volume (thickness × area).
Extraction requirements:
- Unit in g/cm³.
- Exclude overall density values that include the current collector.
"""

Artificial_SEI_Thickness = """
Definition: The thickness of an artificially constructed SEI layer (polymer, ceramic, or composite layer), in μm. Measured by cross-sectional SEM or TEM.
Extraction requirements:
- Unit in μm.
- For multilayer structures, record each layer separately or report the total thickness.
"""

Youngs_Modulus = """
Definition: Young's modulus, reflecting material stiffness, in GPa or MPa. Applicable to artificial SEI layers or electrode binders.
Extraction requirements:
- Unit uniformly in GPa (if given in MPa, divide by 1000).
- Must specify the test method (e.g., nanoindentation, tensile testing).
"""

Tensile_Strength = """
Definition: The tensile strength, i.e., the maximum stress a material can withstand before fracture, in MPa.
Extraction requirements:
- Unit in MPa.
- Must specify the test conditions (e.g., strain rate).
"""

Elongation_at_Break = """
Definition: The elongation at break, i.e., the percentage increase in length at fracture (%).
Extraction requirements:
- Unit in %.
- If given as an absolute value (e.g., strain value), convert to percentage.
"""

Electrolyte_Wettability_Contact_Angle = """
Definition: The contact angle (°) of electrolyte on the electrode surface, reflecting wettability; a smaller angle indicates better wettability.
Extraction requirements:
- Unit in °.
- Must specify the test droplet volume and the test environment (e.g., room temperature).
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
