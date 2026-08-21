#!/usr/bin/env python3
"""Validate a target cluster against the targeting policy.

Usage:
  python validate_target_cluster.py <cluster.yaml>

Relies on agent.telegram_ads_operator.target_selection.validate_target_cluster.
"""

import json
import sys

try:
    from agent.telegram_ads_operator.target_selection import (
        validate_cluster as validate_target_cluster,
    )

    if len(sys.argv) < 2:
        print("Usage: python validate_target_cluster.py <cluster.yaml>", file=sys.stderr)
        sys.exit(1)

    # Read cluster spec
    import yaml
    with open(sys.argv[1]) as f:
        cluster_spec = yaml.safe_load(f)

    result = validate_target_cluster(cluster_spec)
    print(json.dumps(result.model_dump() if hasattr(result, "model_dump") else result, indent=2))
except ImportError as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
