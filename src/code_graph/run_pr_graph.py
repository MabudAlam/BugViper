import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).parent.parent))

from code_graph import (
    build_graph,
    changed_files_from_diff,
    extract_pr_call_graph,
    parse_source_files,
    render_blast_radius_markdown,
)
from code_graph.utils import clone_with_token
from ncodereview.batch import (
    _assign_tier,
    _is_low_signal,
    _score_file,
    batch_pr_files,
    filter_blast_radius_for_files,
    filter_call_graph_for_files,
)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    parts = url.rstrip("/").split("/")
    return parts[-4], parts[-3], int(parts[-1])


def gh_run(*args: str) -> str:
    return subprocess.run(
        ["gh"] + list(args), capture_output=True, text=True, check=True
    ).stdout


def verify_cross_file_edge(
    graph: dict,
    caller_file: str,
    callee_file: str,
    repo_path: str,
) -> tuple[bool, str]:
    """Verify that caller_file has a call to a function defined in callee_file."""
    nodes = graph.get('nodes', [])
    edges = graph.get('edges', [])

    # Find function nodes for both files
    caller_fns = {n['id'] for n in nodes if n.get('file') == caller_file and n['type'] == 'Function'}
    callee_fns = {n['id'] for n in nodes if n.get('file') == callee_file and n['type'] == 'Function'}

    # Find CALLS edges from caller to callee
    matching_edges = [e for e in edges if e['type'] == 'CALLS' and e['source'] in caller_fns and e['target'] in callee_fns]
    if not matching_edges:
        return False, f"No CALLS edge from {caller_file} to {callee_file}"

    # Read caller source to verify the call text exists
    caller_path = repo_path / caller_file
    if not caller_path.exists():
        return False, f"Caller file not found: {caller_file}"

    source = caller_path.read_text(encoding='utf-8', errors='ignore')

    verified = []
    missing = []
    for e in matching_edges:
        tgt_node = next((n for n in nodes if n['id'] == e['target']), {})
        callee_name = tgt_node.get('label', '')
        # Check if callee name appears as a call in the source
        pattern = re.compile(r'\b' + re.escape(callee_name) + r'\s*\(')
        if pattern.search(source):
            verified.append(callee_name)
        else:
            missing.append(callee_name)

    if missing:
        return False, f"False positive CALLS edges: {missing} (not found in source)"
    return True, f"Verified {len(verified)} calls: {', '.join(verified[:5])}"


def verify_incoming_edge(
    graph: dict,
    pr_file: str,
    external_file: str,
    repo_path: str,
) -> tuple[bool, str]:
    """Verify external_file has a call to a function defined in pr_file."""
    return verify_cross_file_edge(graph, external_file, pr_file, repo_path)


def verify_outgoing_edge(
    graph: dict,
    pr_file: str,
    external_file: str,
    repo_path: str,
) -> tuple[bool, str]:
    """Verify pr_file has a call to a function defined in external_file."""
    return verify_cross_file_edge(graph, pr_file, external_file, repo_path)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    parts = url.rstrip("/").split("/")
    return parts[-4], parts[-3], int(parts[-1])


def gh_run(*args: str) -> str:
    return subprocess.run(
        ["gh"] + list(args), capture_output=True, text=True, check=True
    ).stdout


async def run(pr_url: str, output_dir: str = "output"):
    owner, repo, pr_number = parse_pr_url(pr_url)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"PR #{pr_number} | {owner}/{repo}")

    token = gh_run("auth", "token").strip()
    diff_text = gh_run("pr", "diff", str(pr_number), "-R", f"{owner}/{repo}")
    head_sha = gh_run("pr", "view", str(pr_number), "-R", f"{owner}/{repo}",
                       "--json", "headRefOid", "--jq", ".headRefOid").strip()

    print(f"  head: {head_sha[:7]}")

    changed_files = changed_files_from_diff(diff_text)
    print(f"  changed files: {len(changed_files)}")
    (out / "diff.patch").write_text(diff_text)

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "repo"
        print("Cloning repo...")
        clone_with_token(token, f"{owner}/{repo}", head_sha, clone_path)

        print("Parsing source files...")
        files, parsed = parse_source_files(str(clone_path))
        print(f"  {len(files)} files parsed")

        print("Building full code graph...")
        graph = build_graph(str(clone_path), files, parsed)
        print(f"  {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
        (out / "code_graph.json").write_text(json.dumps(graph, indent=2))
        print(f"  -> {out / 'code_graph.json'}")

        print(f"\nExtracting PR call graph ({len(changed_files)} changed files)...")
        pr_graph = extract_pr_call_graph(graph, changed_files)
        summary = pr_graph['summary']
        print(f"  internal: {summary['internal_calls']}, incoming: {summary['incoming_calls']}, outgoing: {summary['outgoing_calls']}")
        pr_graph_json = json.dumps(pr_graph, indent=2)
        (out / "pr_graph.json").write_text(pr_graph_json)
        print(f"  -> {out / 'pr_graph.json'}")

        blast_md = render_blast_radius_markdown(pr_graph)
        (out / "blast_radius.md").write_text(blast_md)
        print(f"  -> {out / 'blast_radius.md'}")

        # ── Verify cross-file edges ─────────────────────────────────────
        print("\n── Edge Verification ──")
        per_file = pr_graph.get("per_file", {})
        verified = 0
        false_positives = 0
        sample_edges: list[tuple[str, str, str]] = []  # caller -> callee, type

        # Sample internal edges (cross-file)
        for f, d in per_file.items():
            for edge in d.get("internal_calls", []):
                cf = edge.get("callee_file")
                if cf and cf != f:
                    callee = edge.get("callee_qualified", "?")
                    sample_edges.append((f, cf, f"internal:{callee}"))
                    if len(sample_edges) >= 15:
                        break
            if len(sample_edges) >= 15:
                break

        for caller_f, callee_f, label in sample_edges:
            ok, msg = verify_cross_file_edge(graph, caller_f, callee_f, clone_path)
            status = "✓" if ok else "✗"
            print(f"  {status} {caller_f} -> {callee_f} ({label}): {msg}")
            if ok:
                verified += 1
            else:
                false_positives += 1

        if false_positives == 0:
            print(f"  All {verified} cross-file edges verified ✓")
        else:
            print(f"  {verified} verified, {false_positives} FALSE POSITIVES ✗")

        # Verify incoming edges (external -> PR)
        print("\n  Incoming edges (external -> PR):")
        incoming_sample = 0
        for f, d in per_file.items():
            for edge in d.get("incoming_calls", []):
                ef = edge.get("caller_file")
                if ef and ef not in changed_files:
                    ok, msg = verify_incoming_edge(graph, f, ef, clone_path)
                    status = "✓" if ok else "✗"
                    print(f"    {status} {ef} -> {f}: {msg}")
                    incoming_sample += 1
                    if incoming_sample >= 5:
                        break
            if incoming_sample >= 5:
                break
        if incoming_sample == 0:
            print("    (none sampled)")

        # Verify outgoing edges (PR -> external)
        print("\n  Outgoing edges (PR -> external):")
        outgoing_sample = 0
        for f, d in per_file.items():
            for edge in d.get("outgoing_calls", []):
                ef = edge.get("callee_file")
                if ef and ef not in changed_files:
                    ok, msg = verify_outgoing_edge(graph, f, ef, clone_path)
                    status = "✓" if ok else "✗"
                    print(f"    {status} {f} -> {ef}: {msg}")
                    outgoing_sample += 1
                    if outgoing_sample >= 5:
                        break
            if outgoing_sample >= 5:
                break
        if outgoing_sample == 0:
            print("    (none sampled)")

        # Also check: do internal_only files (no cross-file) actually have no cross-file calls?
        print("\n  Spot-check: files with zero call edges — verify they truly have no cross-file calls:")
        zero_files = [f for f in changed_files if f not in per_file or (not per_file[f]['internal_calls'] and not per_file[f]['incoming_calls'] and not per_file[f]['outgoing_calls'])]
        for f in zero_files[:10]:
            fpath = clone_path / f
            if fpath.exists():
                content = fpath.read_text(encoding='utf-8', errors='ignore')
                # Check if content has any function-like calls at all
                has_imports = 'import' in content or 'require' in content
                has_functions = bool(re.search(r'(?:function\s+\w+|def\s+\w+|const\s+\w+\s*=\s*(?:async\s*)?\()', content))
                has_calls = bool(re.findall(r'\b[a-zA-Z_]\w*\s*\(', content[:2000]))
                if has_functions or (has_imports and has_calls):
                    print(f"  ⚠ {f}: has code that might have call edges (funcs={has_functions} imports={has_imports} calls={has_calls})")
                else:
                    print(f"  ~ {f}: no missed edges (config/data)")
            else:
                print(f"  - {f}: file not in cloned repo")

    # ── Batch and debug ──────────────────────────────────────────────────
    print("\n── Batching ──")
    batches = batch_pr_files(pr_graph, changed_files, review_mode='deep')
    print(f"  {len(batches)} batch(es) for {len(changed_files)} files\n")

    batch_dir = out / "batches"
    batch_dir.mkdir(exist_ok=True)

    # Pre-compute scores and global max for tier display
    high_signal = [f for f in changed_files if not _is_low_signal(f)]
    all_scores = {f: _score_file(f, pr_graph) for f in high_signal}
    max_score = max(all_scores.values()) if all_scores else 1

    for i, batch_files in enumerate(batches):
        bdir = batch_dir / f"batch_{i:02d}"
        bdir.mkdir(exist_ok=True)

        tiers: dict[str, int] = {"critical": 0, "warm": 0, "optional": 0, "low_signal": 0}
        scores: dict[str, float] = {}
        for f in batch_files:
            if _is_low_signal(f):
                tiers["low_signal"] += 1
            else:
                s = all_scores.get(f, 0)
                scores[f] = s
                tiers[_assign_tier(s, max_score)] += 1

        # Internal edges WITHIN this batch
        per_file = pr_graph.get("per_file", {})
        internal_within = 0
        for f in batch_files:
            for edge in per_file.get(f, {}).get("internal_calls", []):
                if edge.get("callee_file") in batch_files:
                    internal_within += 1

        incoming_to_batch = sum(
            len(per_file.get(f, {}).get("incoming_calls", []))
            for f in batch_files
        )
        outgoing_from_batch = sum(
            len(per_file.get(f, {}).get("outgoing_calls", []))
            for f in batch_files
        )

        tot = sum(len(per_file.get(f, {}).get("internal_calls", [])) for f in batch_files)

        print(f"  Batch {i}: {len(batch_files)} files "
              f"| {tiers['critical']}C {tiers['warm']}W {tiers['optional']}O {tiers['low_signal']}L "
              f"| internal={internal_within}/{tot} incoming={incoming_to_batch} outgoing={outgoing_from_batch}")
        for f in batch_files:
            score_str = f" score={scores[f]:.0f}" if f in scores else ""
            print(f"    {f}{score_str}")

        # Save per-batch artifacts
        bgraph = filter_call_graph_for_files(pr_graph_json, batch_files)
        (bdir / "pr_graph.json").write_text(bgraph)
        bmd = filter_blast_radius_for_files(blast_md, batch_files)
        (bdir / "blast_radius.md").write_text(bmd)
        (bdir / "files.txt").write_text("\n".join(batch_files))

    # Summary table
    print(f"\n  Batch files saved to {batch_dir}/")
    print(f"    Each batch has: pr_graph.json, blast_radius.md, files.txt")
    print("\nDone.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python -m code_graph.run_pr_graph <pr_url> [output_dir]")
        sys.exit(1)
    asyncio.run(run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "output"))
