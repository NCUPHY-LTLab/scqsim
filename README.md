# SCQSim

SCQSim is the public, representation-neutral framework for realizing and
studying quantum gates across general N-subsystem superconducting circuits.
It consumes sealed classical quantization artifacts and owns Hamiltonian
construction, numerical Hilbert realization, logical encoding, closed-system
pulse dynamics, gate metrics, and immutable evidence receipts.

## Status

No stable SCQSim release exists. The `main` branch is only the repository
charter until an accepted and stabilized release is promoted. Active V1 work
belongs on `develop` and uses development versions.

The current `develop` candidate is `1.0.0.dev0`. It defines immutable public
contracts and fail-closed source/runtime boundaries; it is intentionally not a
runnable dynamics checkpoint. The first runnable checkpoint will be
`1.0.0.dev1` after the SCNSim portable source handoff is implemented.

SCQSim does not replace SCNSim or SCGSim. Those projects remain independent
classical-model and electromagnetic-model producers. Private device models,
pulses, state maps, targets, and evidence are not public SCQSim package data
or examples.

See [architecture](docs/architecture.md) and [contracts](docs/contracts.md).

## License

Apache-2.0.
