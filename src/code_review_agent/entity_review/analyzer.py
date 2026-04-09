import logging
import time

from .types import (
    ChangeType,
    ClassificationBreakdown,
    ChangeTypeBreakdown,
    EntityReview,
    ReviewResult,
    ReviewStats,
    RiskBreakdown,
    RiskLevel,
    SemanticChange,
    Timing,
)
from .classify import classify_change, ChangeClassification
from .risk import compute_risk_score, is_public_api, score_to_level
from .untangle import untangle

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".rs",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".py",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".rb",
    ".cs",
    ".php",
    ".swift",
    ".kt",
    ".sh",
    ".bash",
    ".tf",
    ".hcl",
    ".vue",
}

LANGUAGE_MAP = {
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".py": "python",
    ".go": "golang",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".rb": "ruby",
    ".cs": "c_sharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".sh": "bash",
    ".bash": "bash",
    ".tf": "hcl",
    ".hcl": "hcl",
    ".vue": "vue",
}


class AnalyzeOptions:
    def __init__(
        self,
        include_dependent_code: bool = False,
        max_dependents_per_entity: int = 5,
        max_dependent_lines: int = 100,
    ):
        self.include_dependent_code = include_dependent_code
        self.max_dependents_per_entity = max_dependents_per_entity
        self.max_dependent_lines = max_dependent_lines


class EntityGraph:
    def __init__(self):
        self.entities: dict[str, dict] = {}
        self.edges: dict[str, list[str]] = {}

    def add_entity(
        self,
        entity_id: str,
        name: str,
        entity_type: str,
        file_path: str,
        start_line: int = 0,
        end_line: int = 0,
    ):
        self.entities[entity_id] = {
            "entity_id": entity_id,
            "name": name,
            "type": entity_type,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
        }

    def add_edge(self, from_id: str, to_id: str) -> None:
        if from_id not in self.edges:
            self.edges[from_id] = []
        if to_id not in self.edges[from_id]:
            self.edges[from_id].append(to_id)

    def get_dependents(self, entity_id: str) -> list[dict]:
        result = []
        for from_id, tos in self.edges.items():
            if entity_id in tos:
                if from_id in self.entities:
                    result.append(self.entities[from_id])
        return result

    def get_dependencies(self, entity_id: str) -> list[dict]:
        if entity_id not in self.edges:
            return []
        result = []
        for to_id in self.edges[entity_id]:
            if to_id in self.entities:
                result.append(self.entities[to_id])
        return result

    def impact_count(self, entity_id: str, max_depth: int = 10000) -> int:
        visited: set[str] = set()
        queue: list[str] = [entity_id]
        count = 0

        while queue and len(visited) < max_depth:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            dependents = self.get_dependents(current)
            for dep in dependents:
                dep_id = dep.get("entity_id")
                if not dep_id:
                    dep_id = self._find_entity_id(dep["name"], dep["file_path"])
                if dep_id and dep_id not in visited:
                    queue.append(dep_id)
                    count += 1

        return count

    def _find_entity_id(self, name: str, file_path: str) -> str | None:
        for eid, info in self.entities.items():
            if info["name"] == name and info["file_path"] == file_path:
                return eid
        return None


class FileChange:
    def __init__(
        self,
        file_path: str,
        status: str,
        before_content: str | None = None,
        after_content: str | None = None,
    ):
        self.file_path = file_path
        self.status = status
        self.before_content = before_content
        self.after_content = after_content


def analyze_file_pairs(file_pairs: list[FileChange]) -> ReviewResult:
    total_start = time.time()
    diff_start = time.time()

    from .extractor import EntityDiffer

    all_changes: list[SemanticChange] = []

    for fp in file_pairs:
        differ = EntityDiffer(
            fp.before_content or "",
            fp.after_content or "",
            fp.file_path,
        )
        changes = differ.compute_diff()
        all_changes.extend(changes)

    diff_ms = int((time.time() - diff_start) * 1000)

    if not all_changes:
        return _empty_result(file_pairs)

    scoring_start = time.time()

    reviews: list[EntityReview] = []

    for change in all_changes:
        classification = classify_change(change)
        after_ref = change.after_content
        pub_api = is_public_api(change.entity_type, change.entity_name, after_ref)

        review = EntityReview(
            entity_id=change.entity_id,
            entity_name=change.entity_name,
            entity_type=change.entity_type,
            file_path=change.file_path,
            change_type=change.change_type,
            classification=classification,
            risk_score=0.0,
            risk_level=RiskLevel.LOW,
            blast_radius=0,
            dependent_count=0,
            dependency_count=0,
            is_public_api=pub_api,
            structural_change=change.structural_change,
            group_id=0,
            start_line=change.start_line,
            end_line=change.end_line,
            before_content=change.before_content,
            after_content=change.after_content,
            dependent_names=[],
            dependency_names=[],
        )

        review.risk_score = compute_risk_score(review, 0)
        review.risk_level = score_to_level(review.risk_score)

        reviews.append(review)

    reviews.sort(key=lambda r: r.risk_score, reverse=True)

    groups = untangle(reviews, [])

    entity_to_group: dict[str, int] = {}
    for group in groups:
        for eid in group.entity_ids:
            entity_to_group[eid] = group.id

    for review in reviews:
        if review.entity_id in entity_to_group:
            review.group_id = entity_to_group[review.entity_id]

    scoring_ms = int((time.time() - scoring_start) * 1000)
    total_ms = int((time.time() - total_start) * 1000)

    stats = _compute_stats(reviews)

    timing = Timing(
        diff_ms=diff_ms,
        list_files_ms=0,
        file_count=len(file_pairs),
        graph_build_ms=0,
        graph_entity_count=0,
        scoring_ms=scoring_ms,
        total_ms=total_ms,
    )

    return ReviewResult(
        entity_reviews=reviews,
        groups=groups,
        stats=stats,
        timing=timing,
    )


def _compute_stats(reviews: list[EntityReview]) -> ReviewStats:
    by_risk = RiskBreakdown()
    by_classification = ClassificationBreakdown()
    by_change = ChangeTypeBreakdown()

    for r in reviews:
        level = r.risk_level
        if level == RiskLevel.CRITICAL:
            by_risk.critical += 1
        elif level == RiskLevel.HIGH:
            by_risk.high += 1
        elif level == RiskLevel.MEDIUM:
            by_risk.medium += 1
        else:
            by_risk.low += 1

        ct = r.change_type
        if ct == ChangeType.ADDED:
            by_change.added += 1
        elif ct == ChangeType.MODIFIED:
            by_change.modified += 1
        elif ct == ChangeType.DELETED:
            by_change.deleted += 1
        elif ct == ChangeType.MOVED:
            by_change.moved += 1
        elif ct == ChangeType.RENAMED:
            by_change.renamed += 1

        c = r.classification
        if c == ChangeClassification.TEXT:
            by_classification.text += 1
        elif c == ChangeClassification.SYNTAX:
            by_classification.syntax += 1
        elif c == ChangeClassification.FUNCTIONAL:
            by_classification.functional += 1
        else:
            by_classification.mixed += 1

    return ReviewStats(
        total_entities=len(reviews),
        by_risk=by_risk,
        by_classification=by_classification,
        by_change_type=by_change,
    )


def _empty_result(file_pairs: list[FileChange]) -> ReviewResult:
    return ReviewResult(
        entity_reviews=[],
        groups=[],
        stats=ReviewStats(
            total_entities=0,
            by_risk=RiskBreakdown(),
            by_classification=ClassificationBreakdown(),
            by_change_type=ChangeTypeBreakdown(),
        ),
        timing=Timing(),
    )
