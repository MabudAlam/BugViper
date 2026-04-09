"""Inspect-style entity review pipeline - plug-and-play module.

This module ports inspect's analyze.rs logic to Python, with:
- Entity-level diff via tree-sitter
- Neo4j-backed dependency graph for blast radius
- ConGra change classification
- Risk scoring + Union-Find grouping
- Full output to /output directory

Architecture mirrors inspect:
  build_context()     → Phases 1-3: entity diff, file listing, graph build
  analyze_with_options() → Phase 4: score, classify, untangle

Can run standalone or alongside existing review_service.py.
"""

import json
import logging
import time
from pathlib import Path

from common.debug_writer import make_review_dir, write_step
from common.diff_parser import split_diff_by_file
from db.code_serarch_layer import CodeSearchService
from db.client import Neo4jClient

from .analyzer import AnalyzeOptions, EntityGraph, FileChange
from .classify import classify_change
from .risk import compute_risk_score, is_public_api, score_to_level
from .types import (
    ChangeClassification,
    ChangeType,
    EntityReview,
    ReviewResult,
    ReviewStats,
    RiskBreakdown,
    RiskLevel,
    SemanticChange,
    ChangeGroup,
    Timing,
    ClassificationBreakdown,
    ChangeTypeBreakdown,
)
from .untangle import untangle
from .extractor import EntityDiffer

logger = logging.getLogger(__name__)


def build_entity_graph_from_neo4j(
    repo_id: str,
    query_service: CodeSearchService,
    file_paths: list[str] | None = None,
    max_symbols: int = 200,
) -> EntityGraph:
    graph = EntityGraph()
    t_start = time.time()

    try:
        all_symbols = query_service.get_repo_entities(
            repo_id, limit=max_symbols, file_paths=file_paths
        )
        logger.info(
            f"[entity_graph] Fetched {len(all_symbols)} entities from Neo4j in {time.time() - t_start:.1f}s"
            + (f" (filtered to {len(file_paths)} files)" if file_paths else "")
        )
    except Exception as e:
        logger.warning(f"[entity_graph] Failed to query Neo4j for symbols: {e}")
        return graph

    t_entities = time.time()
    for sym in all_symbols:
        name = sym.get("name", "")
        path = sym.get("path", "")
        sym_type = sym.get("type", "unknown")
        line = sym.get("line_number", 0)

        entity_id = f"{path}::{sym_type}::{name}"
        graph.add_entity(entity_id, name, sym_type, path, start_line=line, end_line=line)

    logger.info(
        f"[entity_graph] Added {len(graph.entities)} entities in {time.time() - t_entities:.1f}s"
    )

    t_edges = time.time()
    try:
        call_edges = query_service.get_repo_call_edges(repo_id, limit=500, file_paths=file_paths)
        logger.info(
            f"[entity_graph] Fetched {len(call_edges)} call edges in {time.time() - t_edges:.1f}s"
        )
        for edge in call_edges:
            caller_name = edge.get("caller_name", "")
            caller_path = edge.get("caller_path", "")
            callee_name = edge.get("callee_name", "")
            callee_path = edge.get("callee_path", "")

            caller_id = f"{caller_path}::function::{caller_name}"
            callee_id = f"{callee_path}::function::{callee_name}"
            if caller_id in graph.entities and callee_id in graph.entities:
                graph.add_edge(callee_id, caller_id)
        logger.info(f"[entity_graph] Built {len(graph.edges)} edges")
    except Exception as e:
        logger.warning(f"[entity_graph] get_repo_call_edges failed: {e}")

    logger.info(
        f"[entity_graph] Complete: {len(graph.entities)} entities, {len(graph.edges)} edges in {time.time() - t_start:.1f}s total"
    )
    return graph


def analyze_with_neo4j_graph(
    file_pairs: list[FileChange],
    repo_id: str,
    query_service: CodeSearchService,
    options: AnalyzeOptions | None = None,
    review_dir: Path | None = None,
) -> ReviewResult:
    total_start = time.time()
    diff_start = time.time()
    logger.info(f"[entity_review] Starting on {len(file_pairs)} file pairs")

    all_changes: list[SemanticChange] = []

    for i, fp in enumerate(file_pairs):
        from .extractor import EntityDiffer

        logger.debug(f"[entity_review] Diffing file {i + 1}/{len(file_pairs)}: {fp.file_path}")
        differ = EntityDiffer(
            fp.before_content or "",
            fp.after_content or "",
            fp.file_path,
        )
        changes = differ.compute_diff()
        logger.debug(f"[entity_review]   Found {len(changes)} entity changes")
        all_changes.extend(changes)

    diff_ms = int((time.time() - diff_start) * 1000)
    logger.info(
        f"[entity_review] Diff phase complete: {len(all_changes)} total entity changes in {diff_ms}ms"
    )

    if review_dir:
        write_step(
            review_dir,
            "10_entity_diff.json",
            json.dumps(
                [
                    {
                        "entity_id": c.entity_id,
                        "entity_name": c.entity_name,
                        "entity_type": c.entity_type,
                        "file_path": c.file_path,
                        "change_type": c.change_type.value,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "structural_change": c.structural_change,
                    }
                    for c in all_changes
                ],
                indent=2,
            ),
        )

    if not all_changes:
        logger.info("[entity_review] No entity changes found")
        return _empty_result(len(file_pairs))

    graph_start = time.time()
    logger.info(f"[entity_review] Building Neo4j graph for {len(all_changes)} changes...")
    affected_files = list({c.file_path for c in all_changes})
    logger.info(f"[entity_review] Affected files: {affected_files}")
    graph = build_entity_graph_from_neo4j(repo_id, query_service, file_paths=affected_files)
    graph_build_ms = int((time.time() - graph_start) * 1000)
    total_entities_in_graph = len(graph.entities)
    logger.info(
        f"[entity_review] Graph built: {total_entities_in_graph} entities, {len(graph.edges)} edges in {graph_build_ms}ms"
    )

    scoring_start = time.time()

    reviews: list[EntityReview] = []
    dependency_edges: list[tuple[str, str]] = []

    for i, change in enumerate(all_changes):
        if i % 5 == 0:
            logger.debug(f"[entity_review] Scoring {i + 1}/{len(all_changes)}")
        dependents = graph.get_dependents(change.entity_id)
        dependencies = graph.get_dependencies(change.entity_id)
        blast_radius = graph.impact_count(change.entity_id, 10000)

        classification = classify_change(change)
        after_ref = change.after_content
        pub_api = is_public_api(change.entity_type, change.entity_name, after_ref)

        dependent_names = [(d["name"], d["file_path"]) for d in dependents]
        dependency_names = [(d["name"], d["file_path"]) for d in dependencies]

        review = EntityReview(
            entity_id=change.entity_id,
            entity_name=change.entity_name,
            entity_type=change.entity_type,
            file_path=change.file_path,
            change_type=change.change_type,
            classification=classification,
            risk_score=0.0,
            risk_level=RiskLevel.LOW,
            blast_radius=blast_radius,
            dependent_count=len(dependents),
            dependency_count=len(dependencies),
            is_public_api=pub_api,
            structural_change=change.structural_change,
            group_id=0,
            start_line=change.start_line,
            end_line=change.end_line,
            before_content=change.before_content,
            after_content=change.after_content,
            dependent_names=dependent_names,
            dependency_names=dependency_names,
        )

        review.risk_score = compute_risk_score(review, total_entities_in_graph)
        review.risk_level = score_to_level(review.risk_score)

        reviews.append(review)

        for dep in dependencies:
            dep_id = f"{dep['file_path']}::{dep['type']}::{dep['name']}"
            edge = (change.entity_id, dep_id)
            if edge not in dependency_edges:
                dependency_edges.append(edge)

        for dep in dependents:
            dep_id = f"{dep['file_path']}::{dep['type']}::{dep['name']}"
            edge = (dep_id, change.entity_id)
            if edge not in dependency_edges:
                dependency_edges.append(edge)

    reviews.sort(key=lambda r: r.risk_score, reverse=True)

    logger.info(f"[entity_review] Running Union-Find grouping on {len(reviews)} entities...")
    groups = untangle(reviews, dependency_edges)
    logger.info(f"[entity_review] Grouping complete: {len(groups)} groups found")

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
        graph_build_ms=graph_build_ms,
        graph_entity_count=total_entities_in_graph,
        scoring_ms=scoring_ms,
        total_ms=total_ms,
    )

    result = ReviewResult(
        entity_reviews=reviews,
        groups=groups,
        stats=stats,
        timing=timing,
    )

    if review_dir:
        _write_entity_review_outputs(result, review_dir)

    logger.info(
        f"[entity_review] Complete: {len(reviews)} reviews, "
        f"crit={stats.by_risk.critical} high={stats.by_risk.high} "
        f"med={stats.by_risk.medium} low={stats.by_risk.low} "
        f"in {total_ms}ms total"
    )
    return result


def _write_entity_review_outputs(result: ReviewResult, review_dir: Path) -> None:
    write_step(review_dir, "11_entity_reviews.json", json.dumps(result.to_dict(), indent=2))

    lines = [
        "# Entity Review Results",
        "",
        f"**Total entities:** {result.stats.total_entities}",
        f"**Groups:** {len(result.groups)}",
        "",
        "## Risk Breakdown",
        f"- Critical: {result.stats.by_risk.critical}",
        f"- High: {result.stats.by_risk.high}",
        f"- Medium: {result.stats.by_risk.medium}",
        f"- Low: {result.stats.by_risk.low}",
        "",
        "## Classification Breakdown",
        f"- Text: {result.stats.by_classification.text}",
        f"- Syntax: {result.stats.by_classification.syntax}",
        f"- Functional: {result.stats.by_classification.functional}",
        f"- Mixed: {result.stats.by_classification.mixed}",
        "",
        "## Timing",
        f"- Diff: {result.timing.diff_ms}ms",
        f"- Graph build: {result.timing.graph_build_ms}ms",
        f"- Scoring: {result.timing.scoring_ms}ms",
        f"- Total: {result.timing.total_ms}ms",
        f"- Graph entities: {result.timing.graph_entity_count}",
        "",
        "## Entities (by risk)",
        "",
    ]

    for r in result.entity_reviews:
        risk_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
            r.risk_level.value, "⚪"
        )
        lines.append(
            f"{risk_icon} **{r.risk_level.value.upper()}** {r.entity_type} "
            f"{r.entity_name} ({r.file_path}:{r.start_line})"
        )
        lines.append(
            f"   classification: {r.classification.value}  "
            f"score: {r.risk_score:.3f}  "
            f"blast: {r.blast_radius}  "
            f"deps: {r.dependent_count}/{r.dependency_count}"
        )
        if r.is_public_api:
            lines.append("   public API")
        lines.append("")

    if result.groups:
        lines.append("## Groups")
        for g in result.groups:
            lines.append(f"**Group {g.id}:** {g.label} ({len(g.entity_ids)} entities)")
        lines.append("")

    write_step(review_dir, "12_entity_review_summary.md", "\n".join(lines))


def _compute_stats(reviews: list[EntityReview]) -> ReviewStats:
    by_risk = RiskBreakdown()
    by_classification = _ClassificationBreakdown()
    by_change = _ChangeTypeBreakdown()

    for r in reviews:
        if r.risk_level == RiskLevel.CRITICAL:
            by_risk.critical += 1
        elif r.risk_level == RiskLevel.HIGH:
            by_risk.high += 1
        elif r.risk_level == RiskLevel.MEDIUM:
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


class _ClassificationBreakdown:
    def __init__(self, text: int = 0, syntax: int = 0, functional: int = 0, mixed: int = 0):
        self.text = text
        self.syntax = syntax
        self.functional = functional
        self.mixed = mixed


class _ChangeTypeBreakdown:
    def __init__(
        self, added: int = 0, modified: int = 0, deleted: int = 0, moved: int = 0, renamed: int = 0
    ):
        self.added = added
        self.modified = modified
        self.deleted = deleted
        self.moved = moved
        self.renamed = renamed


def _empty_result(file_count: int) -> ReviewResult:
    return ReviewResult(
        entity_reviews=[],
        groups=[],
        stats=ReviewStats(
            total_entities=0,
            by_risk=RiskBreakdown(),
            by_classification=_ClassificationBreakdown(),
            by_change_type=_ChangeTypeBreakdown(),
        ),
        timing=Timing(),
    )


def run_entity_review_pipeline(
    diff_text: str,
    repo_id: str,
    neo4j: Neo4jClient | None,
    files_changed_all: list[str],
    pr_files: dict[str, str],
    review_dir: Path | None = None,
) -> ReviewResult:
    if review_dir is None:
        review_dir = make_review_dir("", "", 0)

    write_step(review_dir, "20_entity_diff.md", f"# Diff\n```diff\n{diff_text}\n```")

    file_diffs = split_diff_by_file(diff_text)

    file_pairs: list[FileChange] = []
    for fp in files_changed_all:
        before = ""
        after = pr_files.get(fp, "")

        patch = file_diffs.get(fp, "")
        if patch:
            before = _extract_before_content(patch, after)

        status = "modified"
        if fp not in pr_files:
            status = "deleted"
        elif not before and after:
            status = "added"

        file_pairs.append(
            FileChange(
                file_path=fp,
                status=status,
                before_content=before,
                after_content=after,
            )
        )

    query_service = None
    if neo4j is not None:
        query_service = CodeSearchService(neo4j)

    result = analyze_with_neo4j_graph(file_pairs, repo_id, query_service, None, review_dir)

    write_step(review_dir, "21_entity_stats.json", json.dumps(result.stats.to_dict(), indent=2))

    return result


def _extract_before_content(patch: str, after_content: str) -> str:
    lines = after_content.split("\n")
    result_lines: list[str] = []
    for line in patch.splitlines():
        if line.startswith("-"):
            result_lines.append(line[1:])
        elif line.startswith("+"):
            pass
        elif line.startswith(" "):
            result_lines.append(line[1:])
    while result_lines and result_lines[-1] == "":
        result_lines.pop()
    return "\n".join(result_lines)
