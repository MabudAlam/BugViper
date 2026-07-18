"""One-time migration: recompute analytics for all repos with new schema fields.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/migrate_analytics.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger("migrate_analytics")


def _count_resolved(issues: list[dict]) -> int:
    return sum(1 for i in issues if i.get("status") in ("fixed", "resolved"))


async def migrate_all():
    import firebase_admin
    from firebase_admin import credentials, firestore

    cert_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if cert_path and os.path.exists(cert_path):
        firebase_admin.initialize_app(credentials.Certificate(cert_path))
    else:
        firebase_admin.initialize_app()

    db = firestore.client()

    users = list(db.collection("users").stream())
    logger.info("Found %d users", len(users))

    total_repos = 0
    total_prs = 0

    for user_doc in users:
        uid = user_doc.id
        repos = list(db.collection("users").document(uid).collection("repos").stream())
        logger.info("User %s: %d repos", uid, len(repos))

        for repo_doc in repos:
            repo_key = repo_doc.id
            if "_" not in repo_key:
                continue
            owner, repo_name = repo_key.split("_", 1)
            pr_docs = list(
                db.collection("users")
                .document(uid)
                .collection("repos")
                .document(repo_key)
                .collection("prs")
                .stream()
            )

            for pr_doc in pr_docs:
                pr_number = int(pr_doc.id)

                # Recompute analytics for this PR
                pr_ref = (
                    db.collection("users")
                    .document(uid)
                    .collection("repos")
                    .document(repo_key)
                    .collection("prs")
                    .document(str(pr_number))
                )
                analytics_ref = (
                    db.collection("users")
                    .document(uid)
                    .collection("repos")
                    .document(repo_key)
                    .collection("analytics")
                    .document("summary")
                )

                runs = list(pr_ref.collection("reviews").stream())

                pr_runs = []
                for run_doc in runs:
                    run = run_doc.to_dict()
                    issues = run.get("issues", [])
                    pr_runs.append({
                        "run_number": run.get("runNumber", 0),
                        "issues": len(issues),
                        "resolved": _count_resolved(issues),
                    })

                total_issues = sum(r["issues"] for r in pr_runs)
                total_resolved = sum(r["resolved"] for r in pr_runs)
                positives = sum(r.to_dict().get("positivesCount", 0) for r in runs)

                now = datetime.now(timezone.utc).isoformat()
                pr_data = pr_doc.to_dict() or {}
                latest_run = runs[-1].to_dict() if runs else {}

                pr_entry = {
                    "prNumber": pr_number,
                    "owner": pr_data.get("owner", owner),
                    "repo": pr_data.get("repo", repo_name),
                    "repoId": pr_data.get("repoId", ""),
                    "createdAt": pr_data.get("createdAt") or latest_run.get("createdAt"),
                    "lastReviewedAt": latest_run.get("endedAt") or pr_data.get("lastReviewedAt"),
                    "lastReviewType": pr_data.get("lastReviewType"),
                    "lastReviewedSha": pr_data.get("lastReviewedSha"),
                    "reviewStatus": pr_data.get("reviewStatus") or latest_run.get("reviewStatusOverride"),
                    "runs": pr_runs,
                    "mergedAt": pr_data.get("mergedAt"),
                    "closedAt": pr_data.get("closedAt"),
                    "totalIssues": total_issues,
                    "totalResolved": total_resolved,
                    "positives": positives,
                }

                existing = analytics_ref.get()
                analytics = existing.to_dict() if existing.exists else {
                    "owner": owner,
                    "repoName": repo_name,
                    "totalPrs": 0,
                    "totalReviews": 0,
                    "totalIssuesGenerated": 0,
                    "totalIssuesResolved": 0,
                    "totalPositives": 0,
                    "prs": {},
                    "updatedAt": now,
                    "prsPerWeek": 0.0,
                    "addressedRate": 0.0,
                    "avgMergeTimeHours": 0.0,
                    "dailyBreakdown": [],
                }

                prs = analytics.get("prs", {})
                prs[str(pr_number)] = pr_entry
                analytics["prs"] = prs
                analytics["totalPrs"] = len(prs)

                total_reviews = 0
                total_issues_generated = 0
                total_issues_resolved = 0
                total_positives = 0
                for p in prs.values():
                    total_reviews += len(p.get("runs", []))
                    total_issues_generated += p.get("totalIssues", 0)
                    total_issues_resolved += p.get("totalResolved", 0)
                    total_positives += p.get("positives", 0)

                analytics["totalReviews"] = total_reviews
                analytics["totalIssuesGenerated"] = total_issues_generated
                analytics["totalIssuesResolved"] = total_issues_resolved
                analytics["totalPositives"] = total_positives

                # Derived stats
                created_dates = []
                merge_times = []
                for p in prs.values():
                    if p.get("createdAt"):
                        try:
                            created_dates.append(datetime.fromisoformat(p["createdAt"]))
                        except Exception:
                            pass
                    end_time = p.get("mergedAt")
                    if not end_time and not p.get("closedAt"):
                        end_time = p.get("lastReviewedAt")
                    if p.get("createdAt") and end_time:
                        try:
                            delta = (
                                datetime.fromisoformat(end_time)
                                - datetime.fromisoformat(p["createdAt"])
                            )
                            merge_times.append(delta.total_seconds() / 3600)
                        except Exception:
                            pass

                if created_dates:
                    span_days = (datetime.now(timezone.utc) - min(created_dates)).total_seconds() / 86400
                    total = len(created_dates)
                    if span_days < 7:
                        analytics["prsPerWeek"] = float(total)
                    else:
                        analytics["prsPerWeek"] = round(total / span_days * 7, 1)
                else:
                    analytics["prsPerWeek"] = 0.0

                analytics["addressedRate"] = round(
                    total_issues_resolved / max(total_issues_generated, 1), 3
                )
                analytics["avgMergeTimeHours"] = round(
                    sum(merge_times) / max(len(merge_times), 1), 1
                ) if merge_times else 0.0

                # Daily breakdown
                daily = {}
                for p in prs.values():
                    pr_num = p.get("prNumber") if isinstance(p, dict) else getattr(p, "pr_number", None)
                    if not pr_num:
                        continue
                    pr_ref_for_run = (
                        db.collection("users").document(uid)
                        .collection("repos").document(repo_key)
                        .collection("prs").document(str(pr_num))
                    )
                    pr_created = (p.get("createdAt") or "")[:10]
                    for run in p.get("runs", []):
                        run_num = run.get("runNumber", 0) if isinstance(run, dict) else run.run_number
                        run_ref = pr_ref_for_run.collection("reviews").document(f"run_{run_num}").get()
                        if not run_ref.exists:
                            continue
                        run_data = run_ref.to_dict()
                        date_key = (
                            run_data.get("startedAt")
                            or run_data.get("endedAt")
                            or run_data.get("createdAt")
                            or pr_created
                        )[:10]
                        if not date_key:
                            continue
                        if date_key not in daily:
                            daily[date_key] = {"caught": 0, "resolved": 0, "reviews": 0}
                        daily[date_key]["caught"] += run_data.get("issuesCount", 0) if isinstance(run_data, dict) else 0
                        daily[date_key]["resolved"] += _count_resolved(run_data.get("issues", []))
                        daily[date_key]["reviews"] += 1
                analytics["dailyBreakdown"] = [
                    {"date": d, **v} for d, v in sorted(daily.items())
                ]

                analytics["updatedAt"] = now
                analytics_ref.set(analytics)
                total_prs += 1

            total_repos += 1
            logger.info("  %s (%d PRs)", repo_key, len(pr_docs))

    logger.info("Done. %d repos, %d PRs migrated.", total_repos, total_prs)


if __name__ == "__main__":
    asyncio.run(migrate_all())
