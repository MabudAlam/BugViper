"""Review data normalization and validation."""

from __future__ import annotations

import json

from ncodereview.dedup import deduplicate_issues
from ncodereview.diff import get_changed_line_ranges, overlaps_added_lines


def normalize_and_validate_review_data(
    review_data: dict,
    diff_text: str,
    changed_files: list[str],
) -> dict:
    changed_set = set(changed_files)
    added_ranges = get_changed_line_ranges(diff_text)
    issues = flatten_issues(review_data.get("issues", []))
    filtered: list[dict] = []
    judgment_counts: dict[str, int] = {"valid": 0, "nitpick": 0, "outside-diff": 0, "false": 0}

    for issue in issues:
        normalized = normalize_issue(issue)
        if not normalized:
            continue

        file_path = normalized["file"]

        # File not in PR's changed files → outside-diff
        if file_path not in changed_set:
            normalized["classification"] = "outside-diff"
            normalized["status"] = "new"
            filtered.append(normalized)
            continue

        # Line range doesn't overlap with any diff addition → outside-diff
        if not overlaps_added_lines(
            normalized.get("line_start"),
            normalized.get("line_end"),
            added_ranges.get(file_path, []),
        ):
            normalized["classification"] = "outside-diff"
            normalized["status"] = "new"
            filtered.append(normalized)
            continue

        classification = normalized.get("classification")
        if classification:
            judgment_counts[classification] = judgment_counts.get(classification, 0) + 1

        if int(normalized.get("confidence", 8)) < 7 and classification != "valid":
            normalized["issue_type"] = "Nitpick"
            normalized["severity"] = "low"
            if not classification:
                normalized["classification"] = "nitpick"
                judgment_counts["nitpick"] += 1
        if looks_like_positive_issue(normalized):
            add_positive_from_issue(review_data, normalized)
            continue
        filtered.append(normalized)

    filtered = deduplicate_issues(filtered)
    positives = normalize_positives(review_data.get("positives", []))
    walkthrough = normalize_walkthrough(review_data.get("walkthrough", []), changed_files)
    summary = review_data.get("summary") if isinstance(review_data.get("summary"), str) else ""

    raw_agent_outputs = review_data.get("raw_agent_outputs")
    if not raw_agent_outputs:
        raw_agent_outputs = {"orchestrator-output": json.dumps(
            {k: v for k, v in review_data.items() if k != "raw_agent_outputs"},
            indent=2, default=str,
        )}

    return {
        "summary": summary,
        "issues": filtered,
        "positives": positives,
        "walkthrough": walkthrough,
        "files_changed": changed_files,
        "_judgment_counts": judgment_counts if any(judgment_counts.values()) else None,
        "raw_agent_outputs": raw_agent_outputs,
    }


def flatten_issues(raw_issues) -> list[dict]:
    if not isinstance(raw_issues, list):
        return []
    flattened: list[dict] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        nested = item.get("issues")
        if isinstance(nested, list):
            file_path = item.get("file")
            for issue in nested:
                if isinstance(issue, dict):
                    merged = dict(issue)
                    merged.setdefault("file", file_path)
                    flattened.append(merged)
        else:
            flattened.append(item)
    return flattened


def normalize_issue(issue: dict) -> dict | None:
    file_path = issue.get("file") or issue.get("file_path")
    line_start = to_int(issue.get("line_start"))
    if not isinstance(file_path, str) or not file_path or line_start is None:
        return None
    line_end = to_int(issue.get("line_end")) or line_start
    confidence = to_int(issue.get("confidence"))
    normalized = {
        "file": file_path,
        "line_start": line_start,
        "line_end": max(line_start, line_end),
        "issue_type": str(issue.get("issue_type") or issue.get("category") or "Bug"),
        "category": str(issue.get("category") or "bug").lower(),
        "severity": str(issue.get("severity") or "medium").lower(),
        "title": str(issue.get("title") or "Untitled finding"),
        "description": str(issue.get("description") or ""),
        "suggestion": str(issue.get("suggestion") or ""),
        "impact": str(issue.get("impact") or ""),
        "code_snippet": str(issue.get("code_snippet") or ""),
        "confidence": confidence if confidence is not None else 8,
    }
    classification = issue.get("classification")
    if classification in {"valid", "nitpick", "outside-diff", "false"}:
        normalized["classification"] = classification
        if classification == "false":
            normalized["drop_reason"] = str(issue.get("drop_reason") or "")
    if normalized["severity"] not in {"critical", "high", "medium", "low"}:
        normalized["severity"] = "medium"
    if normalized["category"] in {"correctness", "logic"}:
        normalized["category"] = "bug"
    return normalized


def to_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def looks_like_positive_issue(issue: dict) -> bool:
    text = " ".join(
        str(issue.get(key, "")) for key in ("title", "description", "suggestion", "impact")
    ).lower()
    positive_markers = (
        "positive change", "this is a positive", "good:", "fix:", "fixes ",
        "fixed ", "prevents ", "mitigates ", "validation added", "properly validates",
    )
    return any(marker in text for marker in positive_markers)


def add_positive_from_issue(review_data: dict, issue: dict) -> None:
    positives = review_data.setdefault("positives", [])
    positives.append({"file_path": issue["file"], "positive_finding": [issue["title"]]})


def normalize_positives(raw_positives) -> list:
    if not isinstance(raw_positives, list):
        return []
    positives: list = []
    for item in raw_positives:
        if isinstance(item, str):
            positives.append({"file_path": "", "positive_finding": [item]})
        elif isinstance(item, dict):
            positives.append(item)
    return positives


def positives_to_strings(positives: list) -> list[str]:
    result: list[str] = []
    for p in positives:
        if isinstance(p, str):
            result.append(p)
        elif isinstance(p, dict):
            file_path = p.get("file_path", "")
            findings = p.get("positive_finding", [])
            for f in findings:
                if isinstance(f, str):
                    result.append(f"{file_path}: {f}" if file_path else f)
    return result


def normalize_walkthrough(raw_walkthrough, changed_files: list[str]) -> list[dict]:
    if not isinstance(raw_walkthrough, list):
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for item in raw_walkthrough:
        if not isinstance(item, dict):
            continue
        file_path = item.get("file")
        summary = str(item.get("summary") or "")
        if isinstance(file_path, str) and file_path and summary:
            seen.add(file_path)
            out.append({"file": file_path, "summary": summary})
    return out


def extract_review_from_result(result: dict) -> dict | None:
    structured = result.get("structured_response")
    if structured is not None:
        if isinstance(structured, dict):
            return structured
        if hasattr(structured, "model_dump"):
            return structured.model_dump()
        if hasattr(structured, "dict"):
            return structured.dict()
    text = result.get("content") or result.get("text") or ""
    if messages := result.get("messages"):
        last_msg = messages[-1]
        if hasattr(last_msg, "content"):
            text = last_msg.content
        elif isinstance(last_msg, dict):
            text = last_msg.get("content", text)
    review = _parse_json_from_text(text)
    if review is not None:
        # Also extract raw subagent outputs from the message history.
        # Each subagent result appears as an AIMessage with content containing
        # the raw JSON from that subagent (task result messages).
        raw_outputs: dict[str, str] = {}
        for msg in messages:
            content = ""
            if hasattr(msg, "content"):
                content = msg.content
            elif isinstance(msg, dict):
                content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue
            # Try to parse as JSON — if it has "issues" and/or "positives"
            # at top level it looks like a subagent raw output
            try:
                import json
                parsed = json.loads(content)
                if isinstance(parsed, dict) and ("issues" in parsed or "positives" in parsed):
                    # Determine which subagent from name annotation or first key
                    name = None
                    if hasattr(msg, "name") and msg.name:
                        name = msg.name
                    elif isinstance(msg, dict) and msg.get("name"):
                        name = msg.get("name")
                    key = name if name else _infer_subagent_key(parsed)
                    if key:
                        raw_outputs[key] = content
            except (json.JSONDecodeError, Exception):
                pass
        if raw_outputs and "raw_agent_outputs" not in review:
            review["raw_agent_outputs"] = raw_outputs
    return review


def _infer_subagent_key(parsed: dict) -> str | None:
    """Infer subagent name from the structure of a raw subagent JSON."""
    issues = parsed.get("issues", [])
    positives = parsed.get("positives", [])
    if not issues and not positives:
        return None
    # All subagents return the same shape; use a heuristic based on content
    # The orchestrator prompt tells it to key by subagent name, so if the
    # orchestrator followed instructions, raw_outputs will already be populated.
    # This is a fallback for when the orchestrator didn't include it.
    return "subagent"


def _parse_json_from_text(text: str) -> dict | None:
    import json
    import re
    text = text.strip()
    code_block_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except json.JSONDecodeError:
            pass
    brace_start = text.find("{")
    if brace_start == -1:
        return None
    for try_start in range(brace_start, len(text)):
        try:
            candidate = text[try_start:]
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    return None


def resolve_review_mode(review_type: str) -> str:
    if review_type == "full_review":
        return "full"
    return "incremental"
