from __future__ import annotations

import json
import logging

from e2b import Sandbox

from static_code_review._shared import filter_changed_files, run_in_sandbox

logger = logging.getLogger(__name__)

_ESLINT_EXTENSIONS = (
    ".js", ".ts", ".cjs", ".mjs",
    ".d.cts", ".d.mts", ".jsx", ".tsx",
    ".css", ".vue", ".svelte", ".astro",
    ".graphql", ".gql", ".mdx",
)


def run_eslint(
    sbx: Sandbox, repo_dir: str, config_file: str | None, changed_files: list[str],
    debug_dir: str | None = None,
) -> list[dict]:
    files = filter_changed_files(changed_files, _ESLINT_EXTENSIONS)
    if not files:
        return []

    # Symlink yarn-installed global eslint so flat configs that `import 'eslint'` resolve
    run_in_sandbox(sbx, f"mkdir -p {repo_dir}/node_modules && ln -sfn /home/user/.config/yarn/global/node_modules/eslint {repo_dir}/node_modules/eslint", timeout=10)
    # Install npm deps in all subdirectories that have package.json (for eslint plugins)
    rc_pkg, out_pkg, _ = run_in_sandbox(sbx, f"find {repo_dir} -maxdepth 3 -name package.json -not -path '*/node_modules/*'", timeout=10)
    pkg_dirs = [line.rsplit("/package.json", 1)[0] for line in (out_pkg or "").strip().splitlines() if line.strip()]
    for pkg_dir in pkg_dirs:
        run_in_sandbox(sbx, f"cd {pkg_dir} && npm install --no-audit --no-fund --ignore-scripts 2>/dev/null || true", timeout=120)

    file_list = " ".join(files)
    cmd = f"cd {repo_dir} && eslint --format=json {file_list} || true"
    rc, out, err = run_in_sandbox(sbx, cmd, timeout=180)
    logger.info("eslint rc=%d, stdout=%d bytes, stderr=%d bytes", rc, len(out or ""), len(err or ""))
    if err:
        logger.warning("eslint stderr: %s", err[:1000])

    if not out.strip():
        return []
    findings = []
    try:
        decoder = json.JSONDecoder()
        raw, _ = decoder.raw_decode(out)
    except json.JSONDecodeError as e:
        logger.warning("eslint JSON parse failed: %s", e)
        return findings

    for entry in raw if isinstance(raw, list) else []:
        fpath = entry.get("filePath", "")
        rel_path = fpath.replace(repo_dir + "/", "") if repo_dir in fpath else fpath
        for msg in entry.get("messages", []):
            findings.append({
                "tool": "eslint",
                "file": rel_path,
                "line": msg.get("line", 1),
                "col": msg.get("column", 1),
                "rule": msg.get("ruleId", "unknown"),
                "message": msg.get("message", ""),
                "severity": "error" if msg.get("severity") == 2 else "warning",
            })

    logger.info("eslint: %d findings", len(findings))
    return findings
