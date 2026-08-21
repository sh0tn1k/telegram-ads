#!/usr/bin/env python3
"""Validate campaign brief for Telegram Ads.

Usage: python validate_campaign_brief.py <brief.yaml>

Input YAML format matches the campaign-brief template.
"""

import sys
import json
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from agent.telegram_ads_operator import validate_brief, CampaignBrief


def validate_brief_file(path: str) -> dict:
    """Load and validate a campaign brief file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    brief = CampaignBrief(**data)
    is_valid, errors = validate_brief(brief)

    return {
        "valid": is_valid,
        "errors": errors,
        "ready_for_launch": brief.is_valid_for_submission(),
        "project_id": brief.project_id,
        "targeting_type": brief.targeting_type.value if hasattr(brief.targeting_type, "value") else str(brief.targeting_type),
        "language": brief.language,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_campaign_brief.py <brief.yaml>")
        sys.exit(1)

    result = validate_brief_file(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
