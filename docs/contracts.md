# Candidate V1 contracts

State: `CONVERGING`. Durable tests are not authorized before Human acceptance.

## Node-flux source

The initial source is a full conservative graph. With physical fluxes
`Phi_physical`, independent quantization coordinates `phi`, physical charges
`Q_physical`, and canonical charges `q`:

```text
Phi_physical = T phi
q = T^T Q_physical
C_phi = T^T C_phys T
K_phi = T^T K_phys T
```

Gauge/reference constraints are removed before quantization. `C_phi` must be
full rank on the independent dynamical subspace; pseudoinverses and artificial
small capacitances are forbidden. Dev0 verifies the ranks of `T` and
`T^T C_phys T` exactly over the serialized finite numbers, without a numerical
tolerance.

`K_phys` contains only true linear inductors. Every Josephson branch is sealed
separately as `-EJ cos(phase_map @ phi + static_phase_bias)` with orientation,
`EJ`, external-flux provenance, and `CJ`; `CJ` is counted exactly once in
`C_phys`. A source that identifies a Josephson branch but omits its nonlinear
term is invalid. `C_phys` and `K_linear` are derived canonically from their
digest-bound branch contribution matrices rather than supplied as duplicate
floating-point aggregates. Josephson branch IDs must occur exactly once among
the `CJ` contributions and never in `K_linear` contributions. Linear capacitor,
inductor, and mutual coupling representation IDs must each be covered exactly
once by a matching contribution and its sealed upstream evidence.

Units are exact: flux `Wb`, charge `C`, capacitance `F`, inverse inductance
`H^-1`, and Josephson energy `J`. Process-local QuTiP Hamiltonians use `H/hbar`
in `rad/s` with time in `s`, so `U = exp(-i (H/hbar) t)`. `Hz` and `rad/s` are
never interchangeable.

Controls bind fixed-canonical-basis operator identity, units, orientation, and
static-versus-waveform classification. Nonlinear parameter controls require a
full Hamiltonian rebuild. Initial V1 uses a fixed Hilbert basis; it has no
moving-basis connection term.

## Gate evidence

For computational projector `P` and propagator `U`, the unrenormalized
computational map is `M = P U P`. Per-input survival is `||M |psi>||^2` and
leakage is `1 - survival`; no hidden renormalization is permitted. Raw phase
records its basis, frame, and gauge. A local correction is an explicit,
digest-bound pre/post local-Z artifact. Its permitted parameter family remains
a Human semantic decision before dev1.

The candidate average gate fidelity is

```text
(Tr(M†M) + |Tr(U_target† M)|²) / (d (d + 1))
```

with target dimension `d` recorded. Static ZZ is a separate diagnostic, not a
substitute for time-dependent evolution.

## Dev0 boundary

`1.0.0.dev0` validates these models and fails explicitly before scqubits
construction or QuTiP dynamics. It does not fabricate success or results.
SCNSim must later implement the separate `circuit-quantization-source-v1`
export against this contract. Independently proved frequency-independent
canonical reduction remains excluded pending separate Human acceptance.

The mapping evidence for this candidate is SCNSim `develop` commit
`232ea88b8fcd36569425d7818adc52affd2e02e6`, tree
`ef993d862bd230c38d6ac0a76e128d1c1467b2b2`. Its current Direct response
surfaces are classical boundary evidence, not a canonical source export.

## Pending SCNSim handoff decisions

SCNSim must choose and seal the exact producer payloads for branch contribution
evidence, the rank receipt, and explicit no-flux versus sourced bias/fluxoid
provenance. SCQSim does not infer these facts from names, indices, geometry, or
classical response surfaces.
