"""Explicit local Skill-script demonstration; never imported by the product."""

import argparse
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--baseline")
    parser.add_argument("--current")
    args = parser.parse_args()
    script = Path(__file__).parent / "skills/weekly-retail-review/scripts/build_weekly_review.py"
    if args.check:
        assert script.is_file()
        print("Standalone showcase assets: OK")
        return
    if not args.baseline or not args.current:
        parser.error("--baseline and --current are required")
    spec = importlib.util.spec_from_file_location("weekly_example", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(
        json.dumps(
            module.build_weekly_review(json.loads(args.baseline), json.loads(args.current)),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
