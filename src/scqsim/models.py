"""Immutable public contracts for SCQSim's representation-neutral boundary."""

from __future__ import annotations

import hashlib
import json
import math
import re
import types
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum, StrEnum
from fractions import Fraction
from itertools import pairwise
from types import MappingProxyType
from typing import ClassVar, TypeVar, Union, get_args, get_origin, get_type_hints

_SHA256 = re.compile(r"[0-9a-f]{64}")
_DurableT = TypeVar("_DurableT", bound="DurableModel")


class SCQSimError(Exception):
    """Base exception for explicit SCQSim failures."""


class ArtifactValidationError(SCQSimError, ValueError):
    """A durable artifact violates its schema or integrity contract."""


class DigestMismatchError(ArtifactValidationError):
    """Artifact content does not match its bound SHA-256 digest."""


class RouteMismatchError(ArtifactValidationError):
    """A construction attempted to cross quantization routes."""


class SingularCapacitanceError(ArtifactValidationError):
    """Capacitance is singular on the claimed independent subspace."""


class UnresolvedGaugeError(ArtifactValidationError):
    """Gauge or reference constraints were not eliminated explicitly."""


class UnsupportedNonlinearityError(ArtifactValidationError):
    """A nonlinear branch lacks the complete supported potential contract."""


class IncompleteProvenanceError(ArtifactValidationError):
    """Required source or derivation provenance is absent."""


class ForbiddenSourceEvidenceError(ArtifactValidationError):
    """Classical response evidence was offered as a Hermitian source."""


class CapabilityUnavailableError(SCQSimError, RuntimeError):
    """A requested capability is intentionally unavailable in this candidate."""


class UpstreamContractPendingError(CapabilityUnavailableError):
    """The producer-side portable source implementation is not available."""


def _text(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise ArtifactValidationError(f"{name} must be nonempty and whitespace-normalized")


def _digest(value: str, name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} must be a lowercase SHA-256 digest")


def _required_artifact_ref(value: object, name: str) -> None:
    if not isinstance(value, ArtifactRef):
        raise IncompleteProvenanceError(f"{name} must be an ArtifactRef")


def _optional_artifact_ref(value: object, name: str) -> None:
    if value is not None:
        _required_artifact_ref(value, name)


def _artifact_ref_sequence(values: Iterable[object], name: str) -> None:
    if any(not isinstance(value, ArtifactRef) for value in values):
        raise IncompleteProvenanceError(f"{name} must contain only ArtifactRef values")


def _finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ArtifactValidationError(f"{name} must be finite")


def _probability(value: float, name: str) -> None:
    _finite(value, name)
    if not 0.0 <= value <= 1.0:
        raise ArtifactValidationError(f"{name} must be between 0 and 1")


def _unique(values: Iterable[str], name: str) -> None:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ArtifactValidationError(f"{name} must contain unique IDs")


@dataclass(frozen=True)
class FrozenMap(Mapping[str, object]):
    """Small immutable JSON object used by durable artifact models."""

    _items: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        frozen: list[tuple[str, object]] = []
        for key, value in self._items:
            if not isinstance(key, str):
                raise ArtifactValidationError("JSON object keys must be strings")
            _text(key, "JSON object key")
            frozen.append((key, freeze_json(value)))
        if len({key for key, _ in frozen}) != len(frozen):
            raise ArtifactValidationError("JSON object keys must be unique")
        object.__setattr__(self, "_items", tuple(sorted(frozen)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> FrozenMap:
        return cls(tuple(value.items()))

    def __getitem__(self, key: str) -> object:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


def freeze_json(value: object) -> object:
    """Validate and recursively freeze a JSON-compatible value."""

    if isinstance(value, FrozenMap):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ArtifactValidationError("JSON object keys must be strings")
        return FrozenMap.from_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        _finite(value, "JSON number")
        return value
    raise ArtifactValidationError(f"unsupported JSON value: {type(value).__name__}")


def _json_value(value: object) -> object:
    if isinstance(value, FrozenMap):
        return {key: _json_value(item) for key, item in value._items}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, DurableModel):
        return value.to_dict()
    return value


def canonical_json(value: object) -> str:
    """Return deterministic JSON suitable for durable SHA-256 binding."""

    return json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ArtifactValidationError("JSON object keys must be unique")
    return dict(pairs)


@dataclass(frozen=True)
class DurableModel:
    """Canonical JSON and digest behavior for versioned durable models."""

    SCHEMA_ID: ClassVar[str]
    SCHEMA_VERSION: ClassVar[str] = "1"

    def to_dict(self) -> dict[str, object]:
        body = {item.name: _json_value(getattr(self, item.name)) for item in fields(self)}
        return {"schema_id": self.SCHEMA_ID, "schema_version": self.SCHEMA_VERSION, **body}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls: type[_DurableT], document: Mapping[str, object]) -> _DurableT:
        """Strictly decode one versioned artifact and all nested models."""

        return _decode_model(cls, document)

    @classmethod
    def from_json(cls: type[_DurableT], payload: str) -> _DurableT:
        """Strictly decode canonical JSON, rejecting non-finite constants."""

        try:
            document = json.loads(
                payload,
                object_pairs_hook=_unique_json_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ArtifactValidationError(f"non-finite JSON number: {value}")
                ),
            )
        except json.JSONDecodeError as error:
            raise ArtifactValidationError("artifact is not valid JSON") from error
        if not isinstance(document, Mapping):
            raise ArtifactValidationError("artifact root must be a JSON object")
        return cls.from_dict(document)

    @property
    def digest_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactRef(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.artifact-ref"

    artifact_id: str
    artifact_schema_id: str
    artifact_schema_version: str
    sha256: str

    def __post_init__(self) -> None:
        _text(self.artifact_id, "artifact_id")
        _text(self.artifact_schema_id, "artifact_schema_id")
        _text(self.artifact_schema_version, "artifact_schema_version")
        _digest(self.sha256, "sha256")


def _decode_model(cls: type[_DurableT], document: Mapping[str, object]) -> _DurableT:
    expected_fields = {item.name for item in fields(cls)}
    derived_fields = {"leakage"} if cls.__name__ == "StateSurvival" else set()
    expected_keys = {"schema_id", "schema_version", *expected_fields, *derived_fields}
    if set(document) != expected_keys:
        raise ArtifactValidationError(
            f"{cls.SCHEMA_ID} fields differ: expected {sorted(expected_keys)}"
        )
    if document["schema_id"] != cls.SCHEMA_ID or document["schema_version"] != cls.SCHEMA_VERSION:
        raise ArtifactValidationError(f"unsupported {cls.SCHEMA_ID} schema/version")
    hints = get_type_hints(cls)
    decoded = {
        name: _decode_value(document[name], hints[name], f"{cls.SCHEMA_ID}.{name}")
        for name in expected_fields
    }
    model = cls(**decoded)
    if derived_fields and isinstance(model, StateSurvival):
        leakage = _decode_value(document["leakage"], float, "scqsim.state-survival.leakage")
        if leakage != model.leakage:
            raise ArtifactValidationError("derived leakage does not match survival")
    return model


def _decode_value(value: object, annotation: object, name: str) -> object:
    origin = get_origin(annotation)
    if origin is tuple:
        if not isinstance(value, list):
            raise ArtifactValidationError(f"{name} must be a JSON array")
        arguments = get_args(annotation)
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(_decode_value(item, arguments[0], name) for item in value)
        if len(value) != len(arguments):
            raise ArtifactValidationError(f"{name} has the wrong tuple length")
        return tuple(
            _decode_value(item, item_type, name)
            for item, item_type in zip(value, arguments, strict=True)
        )
    if origin in {types.UnionType, Union}:
        if value is None and type(None) in get_args(annotation):
            return None
        candidates = tuple(item for item in get_args(annotation) if item is not type(None))
        errors: list[ArtifactValidationError] = []
        for candidate in candidates:
            try:
                return _decode_value(value, candidate, name)
            except ArtifactValidationError as error:
                errors.append(error)
        raise ArtifactValidationError(f"{name} does not match its declared type") from errors[-1]
    if annotation is FrozenMap:
        if not isinstance(value, Mapping):
            raise ArtifactValidationError(f"{name} must be a JSON object")
        return FrozenMap.from_mapping(value)
    if isinstance(annotation, type) and issubclass(annotation, DurableModel):
        if not isinstance(value, Mapping):
            raise ArtifactValidationError(f"{name} must be a versioned JSON object")
        return annotation.from_dict(value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as error:
            raise ArtifactValidationError(f"{name} has an unsupported enum value") from error
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ArtifactValidationError(f"{name} must be a JSON number")
        decoded_float = float(value)
        _finite(decoded_float, name)
        return decoded_float
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ArtifactValidationError(f"{name} must be a JSON integer")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ArtifactValidationError(f"{name} must be a JSON boolean")
        return value
    if annotation is str:
        if not isinstance(value, str):
            raise ArtifactValidationError(f"{name} must be a JSON string")
        return value
    raise ArtifactValidationError(f"{name} has an unsupported decoder type")


class RepresentationLayer(StrEnum):
    RETAINED_CLASSICAL_COORDINATE = "retained-classical-coordinate"
    CONSERVATIVE_QUANTIZATION_COORDINATE = "conservative-quantization-coordinate"
    HILBERT_TENSOR_FACTOR = "hilbert-tensor-factor"
    COMPUTATIONAL_SUBSPACE = "computational-subspace"
    DRESSED_PROJECTOR_LINEAGE = "dressed-projector-lineage"
    PHYSICAL_ELEMENT = "physical-element"
    CONTROL = "control"
    ENVIRONMENT = "environment"


class RepresentationKind(StrEnum):
    NODE_FLUX_COORDINATE = "node-flux-coordinate"
    HARMONIC_MODE = "harmonic-mode"
    LINEAR_CAPACITOR_BRANCH = "linear-capacitor-branch"
    LINEAR_INDUCTOR_BRANCH = "linear-inductor-branch"
    MUTUAL_INDUCTIVE_COUPLING = "mutual-inductive-coupling"
    JOSEPHSON_BRANCH = "josephson-branch"
    PORT = "port"
    CONTROL_OPERATOR = "control-operator"
    ENVIRONMENT = "environment"
    HILBERT_FACTOR = "hilbert-factor"
    COMPUTATIONAL_SUBSPACE = "computational-subspace"
    DRESSED_PROJECTOR = "dressed-projector"


class BindingRelation(StrEnum):
    SPANS = "spans"
    REALIZES = "realizes"
    ANCHORS = "anchors"
    CONTROL_ON = "control_on"


@dataclass(frozen=True)
class SubsystemRef(DurableModel):
    """Stable semantic identity with no representation-specific fields."""

    SCHEMA_ID: ClassVar[str] = "scqsim.subsystem-ref"

    subsystem_id: str
    semantic_authority: ArtifactRef

    def __post_init__(self) -> None:
        _text(self.subsystem_id, "subsystem_id")
        _required_artifact_ref(self.semantic_authority, "semantic_authority")


@dataclass(frozen=True)
class RepresentationRef(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.representation-ref"

    representation_id: str
    producer: str
    layer: RepresentationLayer
    kind: RepresentationKind
    artifact: ArtifactRef

    def __post_init__(self) -> None:
        _text(self.representation_id, "representation_id")
        _text(self.producer, "producer")
        _required_artifact_ref(self.artifact, "representation artifact")


@dataclass(frozen=True)
class RepresentationBinding(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.representation-binding"

    binding_id: str
    subsystem_ids: tuple[str, ...]
    representation_ids: tuple[str, ...]
    relation: BindingRelation
    payload_ref: ArtifactRef | None = None
    provenance: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subsystem_ids", tuple(self.subsystem_ids))
        object.__setattr__(self, "representation_ids", tuple(self.representation_ids))
        object.__setattr__(self, "provenance", tuple(self.provenance))
        _text(self.binding_id, "binding_id")
        if not self.subsystem_ids or not self.representation_ids:
            raise ArtifactValidationError(
                "a binding needs at least one subsystem and representation"
            )
        _unique(self.subsystem_ids, "subsystem_ids")
        _unique(self.representation_ids, "representation_ids")
        _optional_artifact_ref(self.payload_ref, "binding payload_ref")
        _artifact_ref_sequence(self.provenance, "binding provenance")


class Unit(StrEnum):
    DIMENSIONLESS = "1"
    WEBER = "Wb"
    COULOMB = "C"
    FARAD = "F"
    INVERSE_HENRY = "H^-1"
    JOULE = "J"
    HERTZ = "Hz"
    RAD_PER_SECOND = "rad/s"
    SECOND = "s"
    RADIAN = "rad"
    RAD_PER_WEBER = "rad/Wb"


@dataclass(frozen=True)
class DenseMatrix(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.dense-matrix"

    row_ids: tuple[str, ...]
    column_ids: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    unit: Unit

    def __post_init__(self) -> None:
        object.__setattr__(self, "row_ids", tuple(self.row_ids))
        object.__setattr__(self, "column_ids", tuple(self.column_ids))
        rows = tuple(tuple(float(value) for value in row) for row in self.values)
        object.__setattr__(self, "values", rows)
        if not self.row_ids or not self.column_ids:
            raise ArtifactValidationError("matrix axes must be nonempty")
        _unique(self.row_ids, "matrix row IDs")
        _unique(self.column_ids, "matrix column IDs")
        if len(rows) != len(self.row_ids) or any(len(row) != len(self.column_ids) for row in rows):
            raise ArtifactValidationError("matrix shape does not match its coordinate IDs")
        for row in rows:
            for value in row:
                _finite(value, "matrix value")

    def require_axes(self, row_ids: tuple[str, ...], column_ids: tuple[str, ...]) -> None:
        if self.row_ids != row_ids or self.column_ids != column_ids:
            raise ArtifactValidationError("matrix axes do not match the declared coordinates")

    def require_symmetric(self) -> None:
        if self.row_ids != self.column_ids:
            raise ArtifactValidationError("symmetric matrix axes must match")
        for row, values in enumerate(self.values):
            for column, value in enumerate(values):
                if value != self.values[column][row]:
                    raise ArtifactValidationError("matrix must be exactly symmetric in sealed form")


def _fraction_matrix(matrix: DenseMatrix) -> tuple[tuple[Fraction, ...], ...]:
    return tuple(tuple(Fraction(value) for value in row) for row in matrix.values)


def _matrix_rank(values: tuple[tuple[Fraction, ...], ...]) -> int:
    rows = [list(row) for row in values]
    rank = 0
    column_count = len(rows[0])
    for column in range(column_count):
        pivot = next((row for row in range(rank, len(rows)) if rows[row][column]), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        divisor = rows[rank][column]
        rows[rank] = [value / divisor for value in rows[rank]]
        for row in range(len(rows)):
            if row != rank and rows[row][column]:
                scale = rows[row][column]
                rows[row] = [
                    value - scale * pivot_value
                    for value, pivot_value in zip(rows[row], rows[rank], strict=True)
                ]
        rank += 1
        if rank == len(rows):
            break
    return rank


def _quadratic_transform(
    physical: DenseMatrix, transform: DenseMatrix
) -> tuple[tuple[Fraction, ...], ...]:
    matrix = _fraction_matrix(physical)
    basis = _fraction_matrix(transform)
    physical_count = len(matrix)
    reduced_count = len(basis[0])
    return tuple(
        tuple(
            sum(
                (
                    basis[left][row] * matrix[left][right] * basis[right][column]
                    for left in range(physical_count)
                    for right in range(physical_count)
                ),
                Fraction(),
            )
            for column in range(reduced_count)
        )
        for row in range(reduced_count)
    )


def _sum_contributions(
    contributions: tuple[MatrixContribution, ...],
    coordinate_ids: tuple[str, ...],
    unit: Unit,
) -> DenseMatrix:
    values = tuple(
        tuple(
            math.fsum(item.matrix.values[row][column] for item in contributions)
            for column in range(len(coordinate_ids))
        )
        for row in range(len(coordinate_ids))
    )
    return DenseMatrix(
        row_ids=coordinate_ids,
        column_ids=coordinate_ids,
        values=values,
        unit=unit,
    )


class GaugeMethod(StrEnum):
    EXPLICIT_INDEPENDENT_TRANSFORM = "explicit-independent-transform"


@dataclass(frozen=True)
class GaugeResolution(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.gauge-resolution"

    physical_coordinate_ids: tuple[str, ...]
    quantization_coordinate_ids: tuple[str, ...]
    physical_flux_from_quantization: DenseMatrix
    removed_constraint_ids: tuple[str, ...]
    reference_ids: tuple[str, ...]
    capacitance_rank: int
    rank_receipt: ArtifactRef
    method: GaugeMethod = GaugeMethod.EXPLICIT_INDEPENDENT_TRANSFORM
    physical_flux_unit: Unit = Unit.WEBER
    canonical_charge_unit: Unit = Unit.COULOMB

    def __post_init__(self) -> None:
        object.__setattr__(self, "physical_coordinate_ids", tuple(self.physical_coordinate_ids))
        object.__setattr__(
            self, "quantization_coordinate_ids", tuple(self.quantization_coordinate_ids)
        )
        object.__setattr__(self, "removed_constraint_ids", tuple(self.removed_constraint_ids))
        object.__setattr__(self, "reference_ids", tuple(self.reference_ids))
        if not self.physical_coordinate_ids or not self.quantization_coordinate_ids:
            raise UnresolvedGaugeError("physical and independent coordinates must be explicit")
        _unique(self.physical_coordinate_ids, "physical coordinate IDs")
        _unique(self.quantization_coordinate_ids, "quantization coordinate IDs")
        self.physical_flux_from_quantization.require_axes(
            self.physical_coordinate_ids, self.quantization_coordinate_ids
        )
        if self.physical_flux_from_quantization.unit is not Unit.DIMENSIONLESS:
            raise ArtifactValidationError("the canonical transform T must be dimensionless")
        if (
            self.physical_flux_unit is not Unit.WEBER
            or self.canonical_charge_unit is not Unit.COULOMB
        ):
            raise ArtifactValidationError("canonical flux/charge units must be Wb and C")
        if self.capacitance_rank != len(self.quantization_coordinate_ids):
            raise SingularCapacitanceError("C is not full rank on the independent subspace")
        _required_artifact_ref(self.rank_receipt, "rank_receipt")

    def validate_capacitance(self, capacitance_phys: DenseMatrix) -> None:
        """Verify T and T^T C_phys T exactly over serialized finite values."""

        transform = _fraction_matrix(self.physical_flux_from_quantization)
        dimension = len(self.quantization_coordinate_ids)
        if _matrix_rank(transform) != dimension:
            raise UnresolvedGaugeError("canonical transform T must have full column rank")
        reduced_capacitance = _quadratic_transform(
            capacitance_phys, self.physical_flux_from_quantization
        )
        computed_rank = _matrix_rank(reduced_capacitance)
        if computed_rank != dimension or computed_rank != self.capacitance_rank:
            raise SingularCapacitanceError("T^T C_phys T is singular on the independent subspace")


@dataclass(frozen=True)
class JosephsonTerm(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.josephson-term"

    branch_id: str
    representation_id: str
    positive_terminal_id: str
    negative_terminal_id: str
    ej_joule: float
    cj_farad: float
    phase_map_rad_per_weber: tuple[float, ...]
    static_phase_bias_rad: float
    external_flux_ref: ArtifactRef

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase_map_rad_per_weber", tuple(self.phase_map_rad_per_weber))
        for value, name in (
            (self.branch_id, "branch_id"),
            (self.representation_id, "representation_id"),
            (self.positive_terminal_id, "positive_terminal_id"),
            (self.negative_terminal_id, "negative_terminal_id"),
        ):
            _text(value, name)
        if self.positive_terminal_id == self.negative_terminal_id:
            raise UnsupportedNonlinearityError("Josephson branch orientation must be explicit")
        _finite(self.ej_joule, "ej_joule")
        _finite(self.cj_farad, "cj_farad")
        if self.ej_joule <= 0.0 or self.cj_farad < 0.0:
            raise UnsupportedNonlinearityError("EJ must be positive and CJ nonnegative")
        for phase_value in (*self.phase_map_rad_per_weber, self.static_phase_bias_rad):
            _finite(phase_value, "Josephson phase value")
        _required_artifact_ref(self.external_flux_ref, "external_flux_ref")


class MatrixContributionKind(StrEnum):
    LINEAR_CAPACITANCE = "linear-capacitance"
    JOSEPHSON_CAPACITANCE = "josephson-capacitance"
    LINEAR_INVERSE_INDUCTANCE = "linear-inverse-inductance"


@dataclass(frozen=True)
class MatrixContribution(DurableModel):
    """Sealed branch contribution used to verify aggregate C and K matrices."""

    SCHEMA_ID: ClassVar[str] = "scqsim.matrix-contribution"

    contribution_id: str
    branch_ids: tuple[str, ...]
    kind: MatrixContributionKind
    matrix: DenseMatrix
    evidence_ref: ArtifactRef

    def __post_init__(self) -> None:
        object.__setattr__(self, "branch_ids", tuple(self.branch_ids))
        _text(self.contribution_id, "contribution_id")
        if not self.branch_ids:
            raise IncompleteProvenanceError("matrix contribution must name its upstream branches")
        _unique(self.branch_ids, "matrix contribution branch IDs")
        _required_artifact_ref(self.evidence_ref, "matrix contribution evidence_ref")


class ControlKind(StrEnum):
    LINEAR_OPERATOR = "linear-operator"
    NONLINEAR_PARAMETER = "nonlinear-parameter"


class ControlClassification(StrEnum):
    STATIC = "static"
    WAVEFORM = "waveform"


@dataclass(frozen=True)
class ControlOperatorSpec(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.control-operator-spec"

    control_id: str
    operator_id: str
    representation_id: str
    basis_coordinate_ids: tuple[str, ...]
    parameter_unit: str
    operator_unit: str
    orientation: str
    sign: int
    kind: ControlKind
    classification: ControlClassification
    operator_ref: ArtifactRef
    requires_full_hamiltonian_rebuild: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "basis_coordinate_ids", tuple(self.basis_coordinate_ids))
        for value, name in (
            (self.control_id, "control_id"),
            (self.operator_id, "operator_id"),
            (self.representation_id, "representation_id"),
            (self.parameter_unit, "parameter_unit"),
            (self.operator_unit, "operator_unit"),
            (self.orientation, "orientation"),
        ):
            _text(value, name)
        if self.sign not in {-1, 1}:
            raise ArtifactValidationError("control sign must be +1 or -1")
        if not self.basis_coordinate_ids:
            raise ArtifactValidationError("control operator must name its fixed canonical basis")
        if (
            self.kind is ControlKind.NONLINEAR_PARAMETER
            and not self.requires_full_hamiltonian_rebuild
        ):
            raise ArtifactValidationError("nonlinear controls must rebuild the full Hamiltonian")
        _required_artifact_ref(self.operator_ref, "control operator_ref")


@dataclass(frozen=True)
class EnvironmentSplit(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.environment-split"

    excluded_representation_ids: tuple[str, ...]
    evidence_ref: ArtifactRef

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "excluded_representation_ids", tuple(self.excluded_representation_ids)
        )
        _unique(self.excluded_representation_ids, "excluded environment representation IDs")
        _required_artifact_ref(self.evidence_ref, "environment evidence_ref")


class SourceRoute(StrEnum):
    SCNSIM_NODE_FLUX = "scnsim.node-flux"


@dataclass(frozen=True)
class CircuitQuantizationSource(DurableModel):
    """Full conservative SCNSim node-flux artifact for SCQSim."""

    SCHEMA_ID: ClassVar[str] = "circuit-quantization-source-v1"

    source_id: str
    route: SourceRoute
    producer: str
    producer_version: str
    subsystems: tuple[SubsystemRef, ...]
    representations: tuple[RepresentationRef, ...]
    bindings: tuple[RepresentationBinding, ...]
    gauge: GaugeResolution
    capacitance_contributions: tuple[MatrixContribution, ...]
    linear_inverse_inductance_contributions: tuple[MatrixContribution, ...]
    josephson_terms: tuple[JosephsonTerm, ...]
    controls: tuple[ControlOperatorSpec, ...]
    environment_split: EnvironmentSplit
    source_receipts: tuple[ArtifactRef, ...]
    provenance: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        for name in (
            "subsystems",
            "representations",
            "bindings",
            "capacitance_contributions",
            "linear_inverse_inductance_contributions",
            "josephson_terms",
            "controls",
            "source_receipts",
            "provenance",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for value, name in (
            (self.source_id, "source_id"),
            (self.producer, "producer"),
            (self.producer_version, "producer_version"),
        ):
            _text(value, name)
        if self.route is not SourceRoute.SCNSIM_NODE_FLUX:
            raise RouteMismatchError("initial V1 accepts only the SCNSim node-flux route")
        if not self.source_receipts or not self.provenance:
            raise IncompleteProvenanceError("source receipts and provenance are required")
        _artifact_ref_sequence(self.source_receipts, "source_receipts")
        _artifact_ref_sequence(self.provenance, "source provenance")
        physical_ids = self.gauge.physical_coordinate_ids
        contribution_ids = tuple(
            item.contribution_id
            for item in (
                *self.capacitance_contributions,
                *self.linear_inverse_inductance_contributions,
            )
        )
        _unique(contribution_ids, "matrix contribution IDs")
        for item in self.capacitance_contributions:
            item.matrix.require_axes(physical_ids, physical_ids)
            item.matrix.require_symmetric()
            if item.matrix.unit is not Unit.FARAD or item.kind not in {
                MatrixContributionKind.LINEAR_CAPACITANCE,
                MatrixContributionKind.JOSEPHSON_CAPACITANCE,
            }:
                raise ArtifactValidationError("C contribution must be capacitance in F")
        for item in self.linear_inverse_inductance_contributions:
            item.matrix.require_axes(physical_ids, physical_ids)
            item.matrix.require_symmetric()
            if (
                item.matrix.unit is not Unit.INVERSE_HENRY
                or item.kind is not MatrixContributionKind.LINEAR_INVERSE_INDUCTANCE
            ):
                raise ArtifactValidationError(
                    "K_linear contribution must be true-linear inverse inductance in H^-1"
                )
        self.gauge.validate_capacitance(self.capacitance_phys)
        subsystem_ids = tuple(item.subsystem_id for item in self.subsystems)
        representation_ids = tuple(item.representation_id for item in self.representations)
        _unique(subsystem_ids, "source subsystem IDs")
        _unique(representation_ids, "source representation IDs")
        _unique((item.binding_id for item in self.bindings), "source binding IDs")
        _unique((item.control_id for item in self.controls), "source control IDs")
        _unique(
            (item.representation_id for item in self.controls),
            "source control representation IDs",
        )
        known_subsystems = set(subsystem_ids)
        known_representations = set(representation_ids)
        capacitor_branch_ids = {
            item.representation_id
            for item in self.representations
            if item.kind is RepresentationKind.LINEAR_CAPACITOR_BRANCH
        }
        inductive_branch_ids = {
            item.representation_id
            for item in self.representations
            if item.kind
            in {
                RepresentationKind.LINEAR_INDUCTOR_BRANCH,
                RepresentationKind.MUTUAL_INDUCTIVE_COUPLING,
            }
        }
        contributed_capacitor_ids = tuple(
            branch_id
            for item in self.capacitance_contributions
            if item.kind is MatrixContributionKind.LINEAR_CAPACITANCE
            for branch_id in item.branch_ids
        )
        contributed_inductive_ids = tuple(
            branch_id
            for item in self.linear_inverse_inductance_contributions
            for branch_id in item.branch_ids
        )
        if set(contributed_capacitor_ids) != capacitor_branch_ids or len(
            contributed_capacitor_ids
        ) != len(capacitor_branch_ids):
            raise IncompleteProvenanceError(
                "linear C contributions must uniquely cover capacitor representations"
            )
        if set(contributed_inductive_ids) != inductive_branch_ids or len(
            contributed_inductive_ids
        ) != len(inductive_branch_ids):
            raise IncompleteProvenanceError(
                "K_linear contributions must uniquely cover inductor and mutual representations"
            )
        for binding in self.bindings:
            if not set(binding.subsystem_ids) <= known_subsystems:
                raise ArtifactValidationError("binding references an unknown subsystem")
            if not set(binding.representation_ids) <= known_representations:
                raise ArtifactValidationError("binding references an unknown representation")
        nonlinear_ids = {
            item.representation_id
            for item in self.representations
            if item.kind is RepresentationKind.JOSEPHSON_BRANCH
        }
        term_ids = {item.representation_id for item in self.josephson_terms}
        if nonlinear_ids != term_ids:
            raise UnsupportedNonlinearityError(
                "every identified Josephson branch needs exactly one complete cosine term"
            )
        if len(term_ids) != len(self.josephson_terms):
            raise UnsupportedNonlinearityError("Josephson terms must be unique by representation")
        josephson_branch_ids = {item.branch_id for item in self.josephson_terms}
        if len(josephson_branch_ids) != len(self.josephson_terms):
            raise UnsupportedNonlinearityError("Josephson terms must be unique by branch")
        cj_branch_ids = tuple(
            branch_id
            for item in self.capacitance_contributions
            if item.kind is MatrixContributionKind.JOSEPHSON_CAPACITANCE
            for branch_id in item.branch_ids
        )
        if set(cj_branch_ids) != josephson_branch_ids or len(cj_branch_ids) != len(
            josephson_branch_ids
        ):
            raise UnsupportedNonlinearityError(
                "each Josephson CJ must occur exactly once in sealed C contributions"
            )
        josephson_by_branch = {item.branch_id: item for item in self.josephson_terms}
        known_terminals = set(physical_ids) | set(self.gauge.reference_ids)
        for contribution in self.capacitance_contributions:
            if contribution.kind is not MatrixContributionKind.JOSEPHSON_CAPACITANCE:
                continue
            if len(contribution.branch_ids) != 1:
                raise UnsupportedNonlinearityError(
                    "each Josephson CJ contribution must contain one branch"
                )
            term = josephson_by_branch[contribution.branch_ids[0]]
            if {term.positive_terminal_id, term.negative_terminal_id} - known_terminals:
                raise UnsupportedNonlinearityError(
                    "Josephson terminals must resolve to physical or reference coordinates"
                )
            expected_cj = [[Fraction() for _ in physical_ids] for _ in physical_ids]
            cj = Fraction(term.cj_farad)
            active = tuple(
                physical_ids.index(terminal)
                for terminal in (term.positive_terminal_id, term.negative_terminal_id)
                if terminal in physical_ids
            )
            for index in active:
                expected_cj[index][index] += cj
            if len(active) == 2:
                left, right = active
                expected_cj[left][right] -= cj
                expected_cj[right][left] -= cj
            if _fraction_matrix(contribution.matrix) != tuple(tuple(row) for row in expected_cj):
                raise UnsupportedNonlinearityError(
                    "Josephson CJ contribution does not match its orientation and value"
                )
        if any(
            josephson_branch_ids & set(item.branch_ids)
            for item in self.capacitance_contributions
            if item.kind is not MatrixContributionKind.JOSEPHSON_CAPACITANCE
        ):
            raise UnsupportedNonlinearityError("Josephson CJ cannot appear in another contribution")
        if any(
            josephson_branch_ids & set(item.branch_ids)
            for item in self.linear_inverse_inductance_contributions
        ):
            raise UnsupportedNonlinearityError(
                "K_linear contributions cannot contain Josephson branches"
            )
        coordinate_count = len(self.gauge.quantization_coordinate_ids)
        if any(
            len(item.phase_map_rad_per_weber) != coordinate_count for item in self.josephson_terms
        ):
            raise UnsupportedNonlinearityError(
                "Josephson phase maps must span quantization coordinates"
            )
        for control in self.controls:
            if control.representation_id not in known_representations:
                raise ArtifactValidationError("control references an unknown representation")
            representation = next(
                item
                for item in self.representations
                if item.representation_id == control.representation_id
            )
            if (
                representation.layer is not RepresentationLayer.CONTROL
                or representation.kind is not RepresentationKind.CONTROL_OPERATOR
            ):
                raise ArtifactValidationError("control must bind a control-operator representation")
            if control.basis_coordinate_ids != self.gauge.quantization_coordinate_ids:
                raise ArtifactValidationError(
                    "control operator basis must match canonical coordinates"
                )
            if not any(
                binding.relation is BindingRelation.CONTROL_ON
                and control.representation_id in binding.representation_ids
                for binding in self.bindings
            ):
                raise ArtifactValidationError("control needs an explicit CONTROL_ON binding")
        control_representation_ids = {
            item.representation_id
            for item in self.representations
            if item.layer is RepresentationLayer.CONTROL
            or item.kind is RepresentationKind.CONTROL_OPERATOR
        }
        if control_representation_ids != {item.representation_id for item in self.controls}:
            raise ArtifactValidationError(
                "every control representation needs exactly one control specification"
            )
        excluded = set(self.environment_split.excluded_representation_ids)
        if not excluded <= known_representations:
            raise ArtifactValidationError("environment split references an unknown representation")
        environment_ids = {
            item.representation_id
            for item in self.representations
            if item.layer is RepresentationLayer.ENVIRONMENT
            or item.kind in {RepresentationKind.PORT, RepresentationKind.ENVIRONMENT}
        }
        if excluded != environment_ids:
            raise ArtifactValidationError("every port/environment must be explicitly excluded")

    def source_ref(self) -> ArtifactRef:
        return artifact_ref(self, self.source_id)

    @property
    def capacitance_phys(self) -> DenseMatrix:
        return _sum_contributions(
            self.capacitance_contributions,
            self.gauge.physical_coordinate_ids,
            Unit.FARAD,
        )

    @property
    def inverse_inductance_linear_phys(self) -> DenseMatrix:
        return _sum_contributions(
            self.linear_inverse_inductance_contributions,
            self.gauge.physical_coordinate_ids,
            Unit.INVERSE_HENRY,
        )


@dataclass(frozen=True)
class HilbertFactorSpec(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.hilbert-factor-spec"

    factor_id: str
    subsystem_ids: tuple[str, ...]
    source_binding_ids: tuple[str, ...]
    representation: RepresentationRef
    realization_binding: RepresentationBinding
    basis: str
    truncation: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "subsystem_ids", tuple(self.subsystem_ids))
        object.__setattr__(self, "source_binding_ids", tuple(self.source_binding_ids))
        _text(self.factor_id, "factor_id")
        _text(self.basis, "basis")
        if not self.subsystem_ids or not self.source_binding_ids:
            raise ArtifactValidationError("a Hilbert factor needs subsystem and source bindings")
        _unique(self.subsystem_ids, "factor subsystem IDs")
        _unique(self.source_binding_ids, "factor source binding IDs")
        if (
            self.representation.layer is not RepresentationLayer.HILBERT_TENSOR_FACTOR
            or self.representation.kind is not RepresentationKind.HILBERT_FACTOR
        ):
            raise ArtifactValidationError("factor representation must be a Hilbert tensor factor")
        if (
            self.realization_binding.relation is not BindingRelation.REALIZES
            or self.realization_binding.subsystem_ids != self.subsystem_ids
            or self.realization_binding.representation_ids
            != (self.representation.representation_id,)
        ):
            raise ArtifactValidationError(
                "factor needs an exact REALIZES binding to its Hilbert representation"
            )
        if self.truncation < 1:
            raise ArtifactValidationError("truncation must be positive")


@dataclass(frozen=True)
class HilbertSpaceNode(DurableModel):
    """Recursive hierarchy: leaves carry factors, parents carry children."""

    SCHEMA_ID: ClassVar[str] = "scqsim.hilbert-space-node"

    node_id: str
    factor: HilbertFactorSpec | None = None
    children: tuple[HilbertSpaceNode, ...] = ()
    truncation: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", tuple(self.children))
        _text(self.node_id, "node_id")
        if (self.factor is None) == (not self.children):
            raise ArtifactValidationError("a hierarchy node must be exactly one leaf or parent")
        if self.factor is not None and self.truncation is not None:
            raise ArtifactValidationError("leaf truncation belongs to its Hilbert factor")
        if self.truncation is not None and self.truncation < 1:
            raise ArtifactValidationError("parent truncation must be positive")
        _unique((child.node_id for child in self.children), "hierarchy child IDs")

    def leaves(self) -> tuple[HilbertFactorSpec, ...]:
        if self.factor is not None:
            return (self.factor,)
        return tuple(factor for child in self.children for factor in child.leaves())


class BasisMode(StrEnum):
    FIXED = "fixed"


@dataclass(frozen=True)
class QuantumRealizationSpec(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.quantum-realization-spec"

    realization_id: str
    source: ArtifactRef
    route: SourceRoute
    hierarchy: HilbertSpaceNode
    tensor_factor_order: tuple[str, ...]
    basis_mode: BasisMode = BasisMode.FIXED
    backend_options: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tensor_factor_order", tuple(self.tensor_factor_order))
        frozen = freeze_json(self.backend_options)
        if not isinstance(frozen, FrozenMap):
            raise ArtifactValidationError("backend_options must be a JSON object")
        object.__setattr__(self, "backend_options", frozen)
        _text(self.realization_id, "realization_id")
        _required_artifact_ref(self.source, "realization source")
        factor_ids = tuple(item.factor_id for item in self.hierarchy.leaves())
        _unique(factor_ids, "Hilbert factor IDs")
        if set(self.tensor_factor_order) != set(factor_ids) or len(self.tensor_factor_order) != len(
            factor_ids
        ):
            raise ArtifactValidationError(
                "tensor_factor_order must contain every factor exactly once"
            )


@dataclass(frozen=True)
class CompiledQuantumModel:
    """Process-local scqubits/QuTiP realization; never a durable artifact."""

    model_id: str
    source: ArtifactRef
    realization: QuantumRealizationSpec
    hamiltonian_rad_per_second: object
    operators: Mapping[str, object]
    control_specs: tuple[ControlOperatorSpec, ...]
    tensor_factor_order: tuple[str, ...]
    backend_versions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _text(self.model_id, "model_id")
        _required_artifact_ref(self.source, "compiled model source")
        if self.source != self.realization.source:
            raise RouteMismatchError("compiled model source differs from its realization source")
        if tuple(self.tensor_factor_order) != self.realization.tensor_factor_order:
            raise ArtifactValidationError("compiled tensor ordering differs from its realization")
        object.__setattr__(self, "operators", MappingProxyType(dict(self.operators)))
        object.__setattr__(self, "control_specs", tuple(self.control_specs))
        object.__setattr__(self, "tensor_factor_order", tuple(self.tensor_factor_order))
        object.__setattr__(self, "backend_versions", tuple(self.backend_versions))


@dataclass(frozen=True)
class LogicalStateLineage(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.logical-state-lineage"

    logical_state_id: str
    projector_ref: ArtifactRef
    subspace_ref: ArtifactRef
    parameter_path_ref: ArtifactRef | None = None

    def __post_init__(self) -> None:
        _text(self.logical_state_id, "logical_state_id")
        _required_artifact_ref(self.projector_ref, "logical projector_ref")
        _required_artifact_ref(self.subspace_ref, "logical subspace_ref")
        _optional_artifact_ref(self.parameter_path_ref, "logical parameter_path_ref")


@dataclass(frozen=True)
class LogicalEncoding(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.logical-encoding"

    encoding_id: str
    realization_ref: ArtifactRef
    states: tuple[LogicalStateLineage, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", tuple(self.states))
        _text(self.encoding_id, "encoding_id")
        _required_artifact_ref(self.realization_ref, "encoding realization_ref")
        if not self.states:
            raise ArtifactValidationError("logical encoding must contain at least one state")
        _unique((item.logical_state_id for item in self.states), "logical state IDs")


@dataclass(frozen=True)
class ControlPoint(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.control-point"

    time_seconds: float
    in_phase: float
    quadrature: float = 0.0

    def __post_init__(self) -> None:
        _finite(self.time_seconds, "time_seconds")
        _finite(self.in_phase, "in_phase")
        _finite(self.quadrature, "quadrature")


@dataclass(frozen=True)
class ControlChannel(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.control-channel"

    control_id: str
    operator_id: str
    control_ref: ArtifactRef
    amplitude_unit: str
    points: tuple[ControlPoint, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(self.points))
        _text(self.control_id, "control_id")
        _text(self.operator_id, "operator_id")
        _text(self.amplitude_unit, "amplitude_unit")
        _required_artifact_ref(self.control_ref, "control channel control_ref")
        if not self.points:
            raise ArtifactValidationError("a control channel needs at least one point")
        times = tuple(point.time_seconds for point in self.points)
        if any(right <= left for left, right in pairwise(times)):
            raise ArtifactValidationError("control times must be strictly increasing")


@dataclass(frozen=True)
class ControlSchedule(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.control-schedule"

    schedule_id: str
    source: ArtifactRef
    channels: tuple[ControlChannel, ...]
    frame_ref: ArtifactRef

    def __post_init__(self) -> None:
        object.__setattr__(self, "channels", tuple(self.channels))
        _text(self.schedule_id, "schedule_id")
        _required_artifact_ref(self.source, "schedule source")
        _required_artifact_ref(self.frame_ref, "schedule frame_ref")
        if not self.channels:
            raise ArtifactValidationError("a control schedule needs at least one channel")
        _unique((item.control_id for item in self.channels), "control IDs")
        if any(
            item.control_ref.artifact_schema_id != ControlOperatorSpec.SCHEMA_ID
            or item.control_ref.artifact_schema_version != ControlOperatorSpec.SCHEMA_VERSION
            for item in self.channels
        ):
            raise ArtifactValidationError("control channels must bind versioned control specs")


@dataclass(frozen=True)
class ClosedSystemStudy(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.closed-system-study"

    study_id: str
    source: ArtifactRef
    realization_ref: ArtifactRef
    encoding: LogicalEncoding
    schedule: ControlSchedule
    initial_logical_state_ids: tuple[str, ...]
    solver_options: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_logical_state_ids", tuple(self.initial_logical_state_ids))
        frozen = freeze_json(self.solver_options)
        if not isinstance(frozen, FrozenMap):
            raise ArtifactValidationError("solver_options must be a JSON object")
        object.__setattr__(self, "solver_options", frozen)
        _text(self.study_id, "study_id")
        _required_artifact_ref(self.source, "study source")
        _required_artifact_ref(self.realization_ref, "study realization_ref")
        _unique(self.initial_logical_state_ids, "initial logical state IDs")
        known_states = {item.logical_state_id for item in self.encoding.states}
        if (
            not self.initial_logical_state_ids
            or not set(self.initial_logical_state_ids) <= known_states
        ):
            raise ArtifactValidationError("initial logical states must belong to the encoding")
        if self.encoding.realization_ref != self.realization_ref:
            raise ArtifactValidationError("encoding and study must bind the same realization")
        if self.schedule.source != self.source:
            raise ArtifactValidationError("schedule and study must bind the same source")


@dataclass(frozen=True)
class StateSurvival(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.state-survival"

    input_state_id: str
    survival: float

    def __post_init__(self) -> None:
        _text(self.input_state_id, "input_state_id")
        _probability(self.survival, "survival")

    @property
    def leakage(self) -> float:
        return 1.0 - self.survival

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.SCHEMA_ID,
            "schema_version": self.SCHEMA_VERSION,
            "input_state_id": self.input_state_id,
            "survival": self.survival,
            "leakage": self.leakage,
        }


class LocalZCorrection(StrEnum):
    NONE = "none"
    EXPLICIT_PRE_POST_LOCAL_Z = "explicit-pre-post-local-z"


class FidelityDefinition(StrEnum):
    UNRENORMALIZED_PROJECTED_AVERAGE = "unrenormalized-projected-average-gate-fidelity"


@dataclass(frozen=True)
class GateResult(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.gate-result"

    result_id: str
    study_ref: ArtifactRef
    computational_projector_ref: ArtifactRef
    propagator_ref: ArtifactRef
    projected_map_ref: ArtifactRef
    target_ref: ArtifactRef
    raw_phase_rad: float
    raw_phase_basis: str
    raw_phase_frame: str
    raw_phase_gauge: str
    local_z_correction: LocalZCorrection
    local_z_correction_ref: ArtifactRef | None
    local_z_corrected_phase_rad: float | None
    survival_by_input: tuple[StateSurvival, ...]
    fidelity: float
    fidelity_definition: FidelityDefinition
    target_dimension: int
    static_zz: float | None = None
    static_zz_unit: Unit | None = None
    diagnostics: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        object.__setattr__(self, "survival_by_input", tuple(self.survival_by_input))
        frozen = freeze_json(self.diagnostics)
        if not isinstance(frozen, FrozenMap):
            raise ArtifactValidationError("diagnostics must be a JSON object")
        object.__setattr__(self, "diagnostics", frozen)
        _text(self.result_id, "result_id")
        for reference, name in (
            (self.study_ref, "gate study_ref"),
            (self.computational_projector_ref, "computational_projector_ref"),
            (self.propagator_ref, "propagator_ref"),
            (self.projected_map_ref, "projected_map_ref"),
            (self.target_ref, "target_ref"),
        ):
            _required_artifact_ref(reference, name)
        _optional_artifact_ref(self.local_z_correction_ref, "local_z_correction_ref")
        for value, name in (
            (self.raw_phase_basis, "raw_phase_basis"),
            (self.raw_phase_frame, "raw_phase_frame"),
            (self.raw_phase_gauge, "raw_phase_gauge"),
        ):
            _text(value, name)
        _finite(self.raw_phase_rad, "raw_phase_rad")
        _probability(self.fidelity, "fidelity")
        if self.target_dimension < 1:
            raise ArtifactValidationError("target_dimension must be positive")
        if not self.survival_by_input:
            raise ArtifactValidationError("gate result must report per-input survival/leakage")
        _unique((item.input_state_id for item in self.survival_by_input), "survival input IDs")
        corrected = self.local_z_correction is not LocalZCorrection.NONE
        if corrected != (
            self.local_z_correction_ref is not None and self.local_z_corrected_phase_rad is not None
        ):
            raise ArtifactValidationError(
                "local-Z correction fields must be present or absent together"
            )
        if self.local_z_corrected_phase_rad is not None:
            _finite(self.local_z_corrected_phase_rad, "local_z_corrected_phase_rad")
        if (self.static_zz is None) != (self.static_zz_unit is None):
            raise ArtifactValidationError(
                "static ZZ value and unit must be present or absent together"
            )
        if self.static_zz is not None:
            _finite(self.static_zz, "static_zz")
            if self.static_zz_unit not in {Unit.HERTZ, Unit.RAD_PER_SECOND}:
                raise ArtifactValidationError("static ZZ unit must distinguish Hz from rad/s")


@dataclass(frozen=True)
class EvidenceReceipt(DurableModel):
    SCHEMA_ID: ClassVar[str] = "scqsim.evidence-receipt"

    receipt_id: str
    source: ArtifactRef
    realization_ref: ArtifactRef
    control_ref: ArtifactRef
    result_ref: ArtifactRef
    input_refs: tuple[ArtifactRef, ...]
    backend_versions: tuple[tuple[str, str], ...]
    created_at: str
    environment: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_refs", tuple(self.input_refs))
        object.__setattr__(self, "backend_versions", tuple(self.backend_versions))
        frozen = freeze_json(self.environment)
        if not isinstance(frozen, FrozenMap):
            raise ArtifactValidationError("environment must be a JSON object")
        object.__setattr__(self, "environment", frozen)
        _text(self.receipt_id, "receipt_id")
        for reference, name in (
            (self.source, "receipt source"),
            (self.realization_ref, "receipt realization_ref"),
            (self.control_ref, "receipt control_ref"),
            (self.result_ref, "receipt result_ref"),
        ):
            _required_artifact_ref(reference, name)
        _artifact_ref_sequence(self.input_refs, "receipt input_refs")
        try:
            instant = datetime.fromisoformat(self.created_at)
        except ValueError as error:
            raise ArtifactValidationError("created_at must be ISO 8601") from error
        if instant.tzinfo is None:
            raise ArtifactValidationError("created_at must include a timezone")
        if not self.input_refs or not self.backend_versions:
            raise IncompleteProvenanceError("receipt inputs and backend versions are required")
        for name, version in self.backend_versions:
            _text(name, "backend name")
            _text(version, "backend version")


def artifact_ref(model: DurableModel, artifact_id: str) -> ArtifactRef:
    """Bind a durable model to its exact canonical JSON digest."""

    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_schema_id=model.SCHEMA_ID,
        artifact_schema_version=model.SCHEMA_VERSION,
        sha256=model.digest_sha256,
    )


def verify_artifact_ref(model: DurableModel, reference: ArtifactRef) -> None:
    """Fail closed when a reference does not bind the exact model bytes."""

    if (
        reference.artifact_schema_id != model.SCHEMA_ID
        or reference.artifact_schema_version != model.SCHEMA_VERSION
    ):
        raise ArtifactValidationError("artifact reference schema/version mismatch")
    if reference.sha256 != model.digest_sha256:
        raise DigestMismatchError("artifact reference digest mismatch")
