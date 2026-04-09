from enum import Enum


class ChangeClassification(str, Enum):
    TEXT = "text"
    SYNTAX = "syntax"
    FUNCTIONAL = "functional"
    TEXT_SYNTAX = "text+syntax"
    TEXT_FUNCTIONAL = "text+functional"
    SYNTAX_FUNCTIONAL = "syntax+functional"
    TEXT_SYNTAX_FUNCTIONAL = "text+syntax+functional"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"
    RENAMED = "renamed"


class ReviewVerdict(str, Enum):
    LIKELY_APPROVABLE = "likely_approvable"
    STANDARD_REVIEW = "standard_review"
    REQUIRES_REVIEW = "requires_review"
    REQUIRES_CAREFUL_REVIEW = "requires_careful_review"


class DependentEntity:
    def __init__(
        self,
        entity_name: str,
        entity_type: str,
        file_path: str,
        start_line: int,
        end_line: int,
        content: str,
        own_dependent_count: int = 0,
        is_public_api: bool = False,
    ):
        self.entity_name = entity_name
        self.entity_type = entity_type
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.content = content
        self.own_dependent_count = own_dependent_count
        self.is_public_api = is_public_api


class EntityReview:
    def __init__(
        self,
        entity_id: str,
        entity_name: str,
        entity_type: str,
        file_path: str,
        change_type: ChangeType,
        classification: ChangeClassification = ChangeClassification.FUNCTIONAL,
        risk_score: float = 0.0,
        risk_level: RiskLevel = RiskLevel.LOW,
        blast_radius: int = 0,
        dependent_count: int = 0,
        dependency_count: int = 0,
        is_public_api: bool = False,
        structural_change: bool | None = None,
        group_id: int = 0,
        start_line: int = 0,
        end_line: int = 0,
        before_content: str | None = None,
        after_content: str | None = None,
        dependent_names: list[tuple[str, str]] | None = None,
        dependency_names: list[tuple[str, str]] | None = None,
        dependent_entities: list[DependentEntity] | None = None,
    ):
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.entity_type = entity_type
        self.file_path = file_path
        self.change_type = change_type
        self.classification = classification
        self.risk_score = risk_score
        self.risk_level = risk_level
        self.blast_radius = blast_radius
        self.dependent_count = dependent_count
        self.dependency_count = dependency_count
        self.is_public_api = is_public_api
        self.structural_change = structural_change
        self.group_id = group_id
        self.start_line = start_line
        self.end_line = end_line
        self.before_content = before_content
        self.after_content = after_content
        self.dependent_names = dependent_names or []
        self.dependency_names = dependency_names or []
        self.dependent_entities = dependent_entities or []

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "file_path": self.file_path,
            "change_type": self.change_type.value,
            "classification": self.classification.value,
            "risk_score": round(self.risk_score, 3),
            "risk_level": self.risk_level.value,
            "blast_radius": self.blast_radius,
            "dependent_count": self.dependent_count,
            "dependency_count": self.dependency_count,
            "is_public_api": self.is_public_api,
            "structural_change": self.structural_change,
            "group_id": self.group_id,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "before_content": self.before_content,
            "after_content": self.after_content,
            "dependent_names": self.dependent_names,
            "dependency_names": self.dependency_names,
        }


class ChangeGroup:
    def __init__(self, id: int, label: str, entity_ids: list[str]):
        self.id = id
        self.label = label
        self.entity_ids = entity_ids

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "entity_ids": self.entity_ids,
        }


class RiskBreakdown:
    def __init__(self, critical: int = 0, high: int = 0, medium: int = 0, low: int = 0):
        self.critical = critical
        self.high = high
        self.medium = medium
        self.low = low


class ClassificationBreakdown:
    def __init__(self, text: int = 0, syntax: int = 0, functional: int = 0, mixed: int = 0):
        self.text = text
        self.syntax = syntax
        self.functional = functional
        self.mixed = mixed


class ChangeTypeBreakdown:
    def __init__(
        self,
        added: int = 0,
        modified: int = 0,
        deleted: int = 0,
        moved: int = 0,
        renamed: int = 0,
    ):
        self.added = added
        self.modified = modified
        self.deleted = deleted
        self.moved = moved
        self.renamed = renamed


class ReviewStats:
    def __init__(
        self,
        total_entities: int = 0,
        by_risk: RiskBreakdown | None = None,
        by_classification: ClassificationBreakdown | None = None,
        by_change_type: ChangeTypeBreakdown | None = None,
    ):
        self.total_entities = total_entities
        self.by_risk = by_risk or RiskBreakdown()
        self.by_classification = by_classification or ClassificationBreakdown()
        self.by_change_type = by_change_type or ChangeTypeBreakdown()

    def to_dict(self) -> dict:
        return {
            "total_entities": self.total_entities,
            "by_risk": {
                "critical": self.by_risk.critical,
                "high": self.by_risk.high,
                "medium": self.by_risk.medium,
                "low": self.by_risk.low,
            },
            "by_classification": {
                "text": self.by_classification.text,
                "syntax": self.by_classification.syntax,
                "functional": self.by_classification.functional,
                "mixed": self.by_classification.mixed,
            },
            "by_change_type": {
                "added": self.by_change_type.added,
                "modified": self.by_change_type.modified,
                "deleted": self.by_change_type.deleted,
                "moved": self.by_change_type.moved,
                "renamed": self.by_change_type.renamed,
            },
        }


class Timing:
    def __init__(
        self,
        diff_ms: int = 0,
        list_files_ms: int = 0,
        file_count: int = 0,
        graph_build_ms: int = 0,
        graph_entity_count: int = 0,
        scoring_ms: int = 0,
        total_ms: int = 0,
    ):
        self.diff_ms = diff_ms
        self.list_files_ms = list_files_ms
        self.file_count = file_count
        self.graph_build_ms = graph_build_ms
        self.graph_entity_count = graph_entity_count
        self.scoring_ms = scoring_ms
        self.total_ms = total_ms

    def to_dict(self) -> dict:
        return {
            "diff_ms": self.diff_ms,
            "list_files_ms": self.list_files_ms,
            "file_count": self.file_count,
            "graph_build_ms": self.graph_build_ms,
            "graph_entity_count": self.graph_entity_count,
            "scoring_ms": self.scoring_ms,
            "total_ms": self.total_ms,
        }


class ReviewResult:
    def __init__(
        self,
        entity_reviews: list[EntityReview] | None = None,
        groups: list[ChangeGroup] | None = None,
        stats: ReviewStats | None = None,
        timing: Timing | None = None,
    ):
        self.entity_reviews = entity_reviews or []
        self.groups = groups or []
        self.stats = stats or ReviewStats()
        self.timing = timing or Timing()

    def to_dict(self) -> dict:
        return {
            "entity_reviews": [r.to_dict() for r in self.entity_reviews],
            "groups": [g.to_dict() for g in self.groups],
            "stats": self.stats.to_dict(),
            "timing": self.timing.to_dict(),
        }


class SemanticChange:
    def __init__(
        self,
        entity_id: str,
        entity_name: str,
        entity_type: str,
        file_path: str,
        change_type: ChangeType,
        before_content: str | None = None,
        after_content: str | None = None,
        structural_change: bool | None = None,
        start_line: int = 0,
        end_line: int = 0,
    ):
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.entity_type = entity_type
        self.file_path = file_path
        self.change_type = change_type
        self.before_content = before_content
        self.after_content = after_content
        self.structural_change = structural_change
        self.start_line = start_line
        self.end_line = end_line


class AtRiskEntity:
    def __init__(
        self,
        entity_name: str,
        entity_type: str,
        file_path: str,
        start_line: int,
        end_line: int,
        content: str,
        risk_level: RiskLevel,
        risk_score: float,
        own_dependent_count: int = 0,
        is_public_api: bool = False,
        is_cross_file: bool = False,
    ):
        self.entity_name = entity_name
        self.entity_type = entity_type
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.content = content
        self.risk_level = risk_level
        self.risk_score = risk_score
        self.own_dependent_count = own_dependent_count
        self.is_public_api = is_public_api
        self.is_cross_file = is_cross_file

    def to_dict(self) -> dict:
        return {
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "risk_level": self.risk_level.value,
            "risk_score": round(self.risk_score, 3),
            "own_dependent_count": self.own_dependent_count,
            "is_public_api": self.is_public_api,
            "is_cross_file": self.is_cross_file,
        }


class ThreatSource:
    def __init__(
        self,
        entity_name: str,
        entity_type: str,
        file_path: str,
        change_type: ChangeType,
        classification: ChangeClassification,
        at_risk: list[AtRiskEntity] | None = None,
    ):
        self.entity_name = entity_name
        self.entity_type = entity_type
        self.file_path = file_path
        self.change_type = change_type
        self.classification = classification
        self.at_risk = at_risk or []

    def to_dict(self) -> dict:
        return {
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "file_path": self.file_path,
            "change_type": self.change_type.value,
            "classification": self.classification.value,
            "at_risk": [a.to_dict() for a in self.at_risk],
        }


class PredictResult:
    def __init__(
        self,
        threats: list[ThreatSource] | None = None,
        total_changes: int = 0,
        total_at_risk: int = 0,
        at_risk_by_level: RiskBreakdown | None = None,
        timing: Timing | None = None,
    ):
        self.threats = threats or []
        self.total_changes = total_changes
        self.total_at_risk = total_at_risk
        self.at_risk_by_level = at_risk_by_level or RiskBreakdown()
        self.timing = timing or Timing()

    def to_dict(self) -> dict:
        return {
            "threats": [t.to_dict() for t in self.threats],
            "total_changes": self.total_changes,
            "total_at_risk": self.total_at_risk,
            "at_risk_by_level": {
                "critical": self.at_risk_by_level.critical,
                "high": self.at_risk_by_level.high,
                "medium": self.at_risk_by_level.medium,
                "low": self.at_risk_by_level.low,
            },
            "timing": self.timing.to_dict(),
        }
