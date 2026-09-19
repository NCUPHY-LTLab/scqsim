# Architecture

SCQSim starts after a classical producer seals a complete conservative source:

```text
SCNSim CircuitQuantizationSource
  -> SCQSim source validation
  -> scqubits Hamiltonian and Hilbert realization
  -> QuTiP closed-system dynamics
  -> gate metrics and immutable evidence receipt
```

Every construction selects one exact source authority. Initial V1 accepts only
the SCNSim node-flux route. A future SCGSim Eigenmode-EPR route requires
separate Human acceptance; the routes may be compared but never mixed,
double-counted, or used as fallbacks.

Durable JSON is decoded with exact schema/version and field sets, then rebound
to its canonical SHA-256 identity. Unknown fields, mutable nested payloads,
non-finite numbers, and digest mismatches fail closed. Backend objects remain
process-local.

Identity layers remain explicit: retained classical coordinate, conservative
quantization coordinate, Hilbert tensor factor, computational subspace, and
dressed-projector lineage. `SubsystemRef` carries stable semantic identity;
`RepresentationRef` carries producer-specific identity; relation-typed
`RepresentationBinding` connects them many-to-many. Backend hierarchy, basis,
truncation, and eigenstate indices never redefine physical identity.
Coupling observables such as resonant, exchange, rotating-wave, and spectral
splitting values remain distinct quantities even when they share identity
references.

Open-system dynamics, multiport BBQ, pulse optimization, frequency-local
reduction, automatic source fusion, private models, and private evidence are
outside initial V1. General N-subsystem contracts do not claim arbitrary-N
computational feasibility.
