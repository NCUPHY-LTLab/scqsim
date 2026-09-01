"""Fail-closed source adapter and dev0 runtime boundary."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ArtifactValidationError,
    BindingRelation,
    CircuitQuantizationSource,
    ClosedSystemStudy,
    CompiledQuantumModel,
    ControlClassification,
    ControlOperatorSpec,
    ControlSchedule,
    DigestMismatchError,
    ForbiddenSourceEvidenceError,
    QuantumRealizationSpec,
    RepresentationKind,
    RepresentationLayer,
    RouteMismatchError,
    SourceRoute,
    UpstreamContractPendingError,
    artifact_ref,
)

SCNSIM_DEVELOP_AUTHORITY = "232ea88b8fcd36569425d7818adc52affd2e02e6"
SCNSIM_DEVELOP_TREE = "ef993d862bd230c38d6ac0a76e128d1c1467b2b2"
FORBIDDEN_HERMITIAN_SOURCE_KINDS = (
    "G/loss",
    "matched load",
    "frequency-local Schur reduction",
    "sampled S/Y/Z",
)


class QuantumSourceAdapter(Protocol):
    route: SourceRoute

    def validate(
        self,
        source: CircuitQuantizationSource,
        realization: QuantumRealizationSpec,
    ) -> None: ...

    def compile(
        self,
        source: CircuitQuantizationSource,
        realization: QuantumRealizationSpec,
    ) -> CompiledQuantumModel: ...


class SCNSimNodeFluxAdapter:
    """Validate the public source contract while producer export converges."""

    route = SourceRoute.SCNSIM_NODE_FLUX

    def decode(self, payload: str, expected_sha256: str) -> CircuitQuantizationSource:
        source = CircuitQuantizationSource.from_json(payload)
        if source.digest_sha256 != expected_sha256:
            raise DigestMismatchError("source JSON does not match its expected digest")
        return source

    def validate(
        self,
        source: CircuitQuantizationSource,
        realization: QuantumRealizationSpec,
    ) -> None:
        if source.route is not self.route or realization.route is not self.route:
            raise RouteMismatchError("source, adapter, and realization must select one route")
        if realization.source != source.source_ref():
            raise ArtifactValidationError("realization does not bind the exact sealed source")
        source_subsystems = {item.subsystem_id for item in source.subsystems}
        source_representations = {item.representation_id: item for item in source.representations}
        source_bindings = {item.binding_id: item for item in source.bindings}
        for factor in realization.hierarchy.leaves():
            if not set(factor.subsystem_ids) <= source_subsystems:
                raise ArtifactValidationError("Hilbert factor references an unknown subsystem")
            try:
                bindings = tuple(source_bindings[item] for item in factor.source_binding_ids)
            except KeyError as error:
                raise ArtifactValidationError(
                    "Hilbert factor references an unknown source binding"
                ) from error
            if any(item.relation is not BindingRelation.SPANS for item in bindings):
                raise ArtifactValidationError("Hilbert factor source bindings must use SPANS")
            if set(factor.subsystem_ids) != {
                subsystem_id for item in bindings for subsystem_id in item.subsystem_ids
            }:
                raise ArtifactValidationError(
                    "Hilbert factor subsystems must equal its SPANS binding coverage"
                )
            representations = tuple(
                source_representations[representation_id]
                for item in bindings
                for representation_id in item.representation_ids
            )
            if any(
                item.layer
                not in {
                    RepresentationLayer.RETAINED_CLASSICAL_COORDINATE,
                    RepresentationLayer.CONSERVATIVE_QUANTIZATION_COORDINATE,
                    RepresentationLayer.PHYSICAL_ELEMENT,
                }
                or item.kind
                not in {
                    RepresentationKind.NODE_FLUX_COORDINATE,
                    RepresentationKind.HARMONIC_MODE,
                    RepresentationKind.LINEAR_CAPACITOR_BRANCH,
                    RepresentationKind.LINEAR_INDUCTOR_BRANCH,
                    RepresentationKind.MUTUAL_INDUCTIVE_COUPLING,
                    RepresentationKind.JOSEPHSON_BRANCH,
                }
                for item in representations
            ):
                raise ArtifactValidationError(
                    "Hilbert factors cannot realize port, control, or environment representations"
                )

    def validate_study(
        self,
        source: CircuitQuantizationSource,
        realization: QuantumRealizationSpec,
        study: ClosedSystemStudy,
    ) -> None:
        self.validate(source, realization)
        if study.source != source.source_ref():
            raise ArtifactValidationError("study does not bind the exact sealed source")
        if study.realization_ref != artifact_ref(realization, realization.realization_id):
            raise ArtifactValidationError("study does not bind the exact realization")
        _validate_schedule(source.controls, study.schedule)

    def reject_classical_response_source(self, evidence_kind: str) -> None:
        raise ForbiddenSourceEvidenceError(
            f"{evidence_kind} is boundary evidence, not a Hermitian Hamiltonian source"
        )

    def compile(
        self,
        source: CircuitQuantizationSource,
        realization: QuantumRealizationSpec,
    ) -> CompiledQuantumModel:
        self.validate(source, realization)
        raise UpstreamContractPendingError(
            "SCNSim circuit-quantization-source-v1 export is not integrated; "
            f"current producer authority is develop {SCNSIM_DEVELOP_AUTHORITY} "
            f"tree {SCNSIM_DEVELOP_TREE}"
        )


def run_closed_system_study(
    model: CompiledQuantumModel,
    study: ClosedSystemStudy,
) -> None:
    """Fail explicitly until the first runnable QuTiP checkpoint (dev1)."""

    if model.source != study.source:
        raise RouteMismatchError(
            "study and process-local model select different source authorities"
        )
    if study.realization_ref != artifact_ref(model.realization, model.realization.realization_id):
        raise RouteMismatchError("study and model select different realizations")
    _validate_schedule(model.control_specs, study.schedule)
    raise UpstreamContractPendingError(
        "closed-system QuTiP execution is unavailable in 1.0.0.dev0; "
        "the SCNSim source export must be integrated before dev1"
    )


def _validate_schedule(
    controls: tuple[ControlOperatorSpec, ...], schedule: ControlSchedule
) -> None:
    specs = {item.control_id: item for item in controls}
    for channel in schedule.channels:
        try:
            spec = specs[channel.control_id]
        except KeyError as error:
            raise ArtifactValidationError(
                "schedule references an unknown source control"
            ) from error
        if (
            channel.operator_id != spec.operator_id
            or channel.amplitude_unit != spec.parameter_unit
            or channel.control_ref != artifact_ref(spec, spec.control_id)
        ):
            raise ArtifactValidationError(
                "schedule control identity, units, or digest differ from the source"
            )
        if spec.classification is ControlClassification.STATIC:
            first = (channel.points[0].in_phase, channel.points[0].quadrature)
            if any((item.in_phase, item.quadrature) != first for item in channel.points[1:]):
                raise ArtifactValidationError("static controls cannot carry a varying waveform")
