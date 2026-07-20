#!/usr/bin/env python3
"""Seed demo data for analytics charts: repos, PRs, and review runs for past 30 days."""

import json
import os
import random
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import credentials, firestore

UID = os.environ.get("DEMO_UID", "sQMAfBMtUag8NFKlYGnqm6ilSSp2")
OWNER = "MabudAlam"

REPOS = [
    {"name": "backend-api", "lang": "Python"},
    {"name": "web-frontend", "lang": "TypeScript"},
    {"name": "cli-tool", "lang": "Rust"},
    {"name": "microservice-auth", "lang": "Go"},
    {"name": "data-pipeline", "lang": "Python"},
    {"name": "mobile-sdk", "lang": "Kotlin"},
    {"name": "docs-site", "lang": "TypeScript"},
    {"name": "infra-terraform", "lang": "HCL"},
]

SEVERITIES = ["low", "medium", "high", "critical"]
CATEGORIES = ["bug", "security", "performance", "style", "best_practice"]

now = datetime.now(timezone.utc)


def random_iso(days_back: int) -> str:
    d = now - timedelta(days=random.uniform(0, days_back), hours=random.uniform(0, 24))
    return d.isoformat()


def gen_issue() -> dict:
    return {
        "title": f"Demo issue #{random.randint(1, 1000)}",
        "description": f"Demo issue for testing charts",
        "suggestion": "Fix this by following best practices.",
        "file": f"src/{random.choice(['main.py', 'utils.ts', 'handlers.go', 'config.rs', 'app.kt'])}",
        "line_start": random.randint(1, 200),
        "line_end": random.randint(1, 200),
        "severity": random.choice(SEVERITIES),
        "category": random.choice(CATEGORIES),
        "confidence": random.randint(5, 10),
        "status": random.choice(["new", "still_open", "fixed"]),
    }


def main():
    cred_path = os.environ.get("SERVICE_FILE_LOC", "secrets/service.json")
    if cred_path.startswith("{"):
        cred = credentials.Certificate(json.loads(cred_path))
    else:
        cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    for repo_def in REPOS:
        repo_name = repo_def["name"]
        repo_key = f"{OWNER}_{repo_name}"
        print(f"\n=== {repo_key} ===")

        repo_data = {
            "owner": OWNER,
            "repoName": repo_name,
            "fullName": f"{OWNER}/{repo_name}",
            "description": f"Demo {repo_name}",
            "language": repo_def["lang"],
            "stars": random.randint(0, 500),
            "forks": random.randint(0, 50),
            "private": random.choice([True, False]),
            "defaultBranch": "main",
            "size": random.randint(1000, 50000),
            "ingestionStatus": "completed",
            "topics": [repo_def["lang"].lower(), "demo", "bugviper"],
            "githubCreatedAt": random_iso(90),
            "githubUpdatedAt": now.isoformat(),
            "createdAt": random_iso(90),
            "updatedAt": now.isoformat(),
        }
        db.collection("users").document(UID).collection("repos").document(repo_key).set(repo_data)

        pr_list = []
        pr_count = random.randint(4, 10)
        used_numbers = set()

        for _ in range(pr_count):
            while (pr_num := random.randint(1, 999)) in used_numbers:
                pass
            used_numbers.add(pr_num)

            created_at = random_iso(30)
            merged = random.random() < 0.5
            merged_at = random_iso(0) if merged else None
            review_count = random.randint(1, 5)

            runs = []
            for rn in range(1, review_count + 1):
                review_start = random_iso(2)
                dur = random.uniform(30, 300)
                end = datetime.fromisoformat(review_start) + timedelta(seconds=dur)
                ic = random.randint(0, 10)
                issues = [gen_issue() for _ in range(ic)]
                runs.append({
                    "issues": issues,
                    "positiveFindings": [f"Pattern #{i}" for i in range(random.randint(0, 4))],
                    "summary": f"Reviewed {random.randint(1, 5)} files",
                    "repoId": repo_key,
                    "prNumber": pr_num,
                    "reviewType": random.choice(["full_review", "incremental_review"]),
                    "issuesCount": ic,
                    "positivesCount": random.randint(0, 5),
                    "startedAt": review_start,
                    "endedAt": end.isoformat(),
                    "durationSeconds": dur,
                })

            pr_list.append({
                "prNumber": pr_num,
                "createdAt": created_at,
                "mergedAt": merged_at,
                "closedAt": merged_at if merged else None,
                "merged": merged,
                "reviewCount": review_count,
                "runs": runs,
            })

        # Write all PRs and runs in batches
        batch_size = 0
        batch = {}
        for pr in pr_list:
            pr_num = pr["prNumber"]
            pr_ref = db.collection("users").document(UID).collection("repos").document(repo_key).collection("prs").document(str(pr_num))
            pr_doc = {
                "owner": OWNER,
                "repo": repo_name,
                "prNumber": pr_num,
                "repoId": repo_key,
                "reviewStatus": "completed",
                "reviewCount": pr["reviewCount"],
                "openIssueCount": random.randint(0, 5),
                "totalIssuesRaised": random.randint(0, 15),
                "totalPositives": random.randint(0, 8),
                "lastReviewType": random.choice(["full_review", "incremental_review"]),
                "lastReviewedSha": "abc" + "".join(random.choices("0123456789abcdef", k=8)),
                "createdAt": pr["createdAt"],
                "updatedAt": now.isoformat(),
                "mergedAt": pr["mergedAt"],
                "closedAt": pr["closedAt"],
            }
            pr_ref.set(pr_doc)

            batch[f"pr_{pr_num}"] = pr_ref

            for i, run in enumerate(pr["runs"]):
                run_ref = pr_ref.collection("reviews").document(f"run_{i + 1}")
                run_ref.set(run)

        # Compute analytics
        all_pr_entries = {}
        total_revs = 0
        total_gen = 0
        total_res = 0
        total_pos = 0

        for pr in pr_list:
            pr_num = pr["prNumber"]
            runs = pr["runs"]
            ti = sum(r.get("issuesCount", 0) or 0 for r in runs)
            tr = sum(sum(1 for j in r.get("issues", []) if j.get("status") == "fixed") for r in runs)
            tp = sum(r.get("positivesCount", 0) or 0 for r in runs)
            total_revs += len(runs)
            total_gen += ti
            total_res += tr
            total_pos += tp

            entry = {
                "prNumber": pr_num,
                "owner": OWNER,
                "repo": repo_name,
                "repoId": repo_key,
                "createdAt": pr["createdAt"],
                "runs": [
                    {"runNumber": i + 1,
                     "issues": r.get("issuesCount", 0) or 0,
                     "resolved": sum(1 for j in r.get("issues", []) if j.get("status") == "fixed")}
                    for i, r in enumerate(runs)
                ],
                "totalIssues": ti,
                "totalResolved": tr,
                "positives": tp,
                "mergedAt": pr["mergedAt"],
                "closedAt": pr["closedAt"],
            }
            all_pr_entries[str(pr_num)] = entry

        # Generate daily breakdown for the past 30 days
        daily = []
        reviews_per_day = max(round(total_revs / 30), 1)
        issues_per_day = max(round(total_gen / 30), 1)
        prs_per_day_val = max(round(len(pr_list) / 30), 1)
        for day_offset in range(30):
            day = (now - timedelta(days=29 - day_offset)).strftime("%Y-%m-%d")
            p = random.randint(0, prs_per_day_val * 2)
            r = random.randint(p, p * 2) if p > 0 else 0
            c = random.randint(0, max(issues_per_day * 2, 1))
            s = random.randint(0, c) if c > 0 else 0
            daily.append({"date": day, "caught": c, "resolved": s, "reviews": r, "prsReviewed": p})

        analytics = {
            "owner": OWNER,
            "repoName": repo_name,
            "totalPrs": len(pr_list),
            "totalReviews": total_revs,
            "totalIssuesGenerated": total_gen,
            "totalIssuesResolved": total_res,
            "totalPositives": total_pos,
            "avgMergeTimeHours": round(random.uniform(2, 48), 1),
            "addressedRate": round(random.uniform(0.3, 0.9), 2),
            "prsPerWeek": round(len(pr_list) / 4.3, 1),
            "dailyBreakdown": daily,
            "prs": all_pr_entries,
            "updatedAt": now.isoformat(),
        }
        db.collection("users").document(UID).collection("repos").document(repo_key).collection("analytics").document("summary").set(analytics)

        print(f"  {len(pr_list)} PRs, {total_revs} reviews, {total_gen} issues, {total_res} resolved")

    print(f"\nDone. Demo data seeded for {UID}")


if __name__ == "__main__":
    main()
