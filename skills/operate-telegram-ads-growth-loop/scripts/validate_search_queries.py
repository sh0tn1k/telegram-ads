#!/usr/bin/env python3
"""Validate search queries for Telegram Ads campaigns.

Usage: python validate_search_queries.py <queries_file.yaml>
       python validate_search_queries.py --stdin
       python validate_search_queries.py "query1" "query2" ...

Input YAML format:
    queries:
      - "ai video clips"
      - "video clipper"
    language: "en"
"""

import sys
import json
from pathlib import Path

# Add repo to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from agent.telegram_ads_operator import validate_cluster, QueryClassification


def validate_queries(queries: list[str], language: str = "") -> dict:
    """Validate a list of search queries and return structured result."""
    result = validate_cluster(queries, language=language)
    return {
        "passed": result.passed,
        "total": len(queries),
        "accepted": result.accepted,
        "needs_review": result.needs_review,
        "rejected": result.rejected,
        "errors": result.errors,
        "warnings": result.warnings,
        "details": [
            {
                "query": q.raw_query,
                "normalized": q.normalized_query,
                "chars": q.character_count,
                "verdict": q.verdict,
                "reasons": q.reasons,
            }
            for q in result.queries
        ],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_search_queries.py <query1> <query2> ...")
        sys.exit(1)

    queries = sys.argv[1:]
    result = validate_queries(queries)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
