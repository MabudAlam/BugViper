import math
from .types import (
    ChangeClassification,
    ChangeType,
    EntityReview,
    ReviewResult,
    RiskLevel,
    ReviewVerdict,
)


def compute_risk_score(review: EntityReview, total_entities: int) -> float:
    score = 0.0

    score += _classification_weight(review.classification)

    score += _change_type_weight(review.change_type)

    if review.is_public_api:
        score += 0.12

    if total_entities > 0 and review.blast_radius > 0:
        blast_ratio = review.blast_radius / total_entities
        score += math.sqrt(blast_ratio) * 0.30

    if review.dependent_count > 0:
        score += math.log(1.0 + review.dependent_count) * 0.15

    if review.structural_change is False:
        score *= 0.2

    return min(score, 1.0)


def score_to_level(score: float) -> RiskLevel:
    if score >= 0.7:
        return RiskLevel.CRITICAL
    if score >= 0.5:
        return RiskLevel.HIGH
    if score >= 0.3:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def suggest_verdict(result: ReviewResult) -> ReviewVerdict:
    if result.stats.by_risk.critical > 0:
        return ReviewVerdict.REQUIRES_CAREFUL_REVIEW
    if result.stats.by_risk.high > 0:
        return ReviewVerdict.REQUIRES_REVIEW

    all_cosmetic = all(
        r.structural_change is False
        for r in result.entity_reviews
        if r.structural_change is not None
    )
    if all_cosmetic and result.stats.total_entities > 0:
        return ReviewVerdict.LIKELY_APPROVABLE

    return ReviewVerdict.STANDARD_REVIEW


def _classification_weight(c: ChangeClassification) -> float:
    weights = {
        ChangeClassification.TEXT: 0.0,
        ChangeClassification.SYNTAX: 0.08,
        ChangeClassification.FUNCTIONAL: 0.22,
        ChangeClassification.TEXT_SYNTAX: 0.1,
        ChangeClassification.TEXT_FUNCTIONAL: 0.22,
        ChangeClassification.SYNTAX_FUNCTIONAL: 0.25,
        ChangeClassification.TEXT_SYNTAX_FUNCTIONAL: 0.28,
    }
    return weights.get(c, 0.0)


def _change_type_weight(ct: ChangeType) -> float:
    weights = {
        ChangeType.DELETED: 0.12,
        ChangeType.MODIFIED: 0.08,
        ChangeType.RENAMED: 0.04,
        ChangeType.MOVED: 0.0,
        ChangeType.ADDED: 0.02,
    }
    return weights.get(ct, 0.0)


def is_public_api(entity_type: str, entity_name: str, content: str | None = None) -> bool:
    if content:
        first_line = content.split("\n")[0] if "\n" in content else content
        first_line = first_line.strip()
        if (
            first_line.startswith("pub ")
            or first_line.startswith("pub(crate)")
            or first_line.startswith("export ")
            or first_line.startswith("module.exports")
        ):
            return True

    if entity_type in ("function", "method", "struct", "interface"):
        if entity_name and entity_name[0].isupper():
            return True

    return False


def rank_dependent(own_dependent_count: int, is_public: bool, is_cross_file: bool) -> float:
    score = math.log(1.0 + own_dependent_count) * 0.5
    if is_public:
        score += 0.3
    if is_cross_file:
        score += 0.2
    return score


def predict_risk_score(
    own_dependent_count: int,
    is_public_api: bool,
    is_cross_file: bool,
    source_classification: ChangeClassification,
    source_change_type: ChangeType,
) -> float:
    score = 0.0
    score += math.log(1.0 + own_dependent_count) * 0.25
    if is_public_api:
        score += 0.20
    if is_cross_file:
        score += 0.15

    if source_classification in (
        ChangeClassification.FUNCTIONAL,
        ChangeClassification.SYNTAX_FUNCTIONAL,
        ChangeClassification.TEXT_SYNTAX_FUNCTIONAL,
        ChangeClassification.TEXT_FUNCTIONAL,
    ):
        score += 0.25
    elif source_classification in (
        ChangeClassification.SYNTAX,
        ChangeClassification.TEXT_SYNTAX,
    ):
        score += 0.15
    else:
        score += 0.0

    if source_change_type == ChangeType.DELETED:
        score += 0.25
    elif source_change_type == ChangeType.MODIFIED:
        score += 0.10
    elif source_change_type == ChangeType.RENAMED:
        score += 0.05

    return min(score, 1.0)
