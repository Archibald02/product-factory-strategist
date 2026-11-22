"""Aggregate observations from paired runs; absent observations stay unknown."""
import argparse
import json
import math
from pathlib import Path


def evaluate(data):
    result = {}
    for mode in ("chat", "agent"):
        row = data[mode]
        seconds = row["seconds"]
        if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0):
            raise ValueError(f"{mode}: invalid seconds")
        values = [row[key] for key in ("human_edits", "verified_claims", "total_claims")]
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
            raise ValueError(f"{mode}: counts must be nonnegative integers")
        edits, verified, total = values
        if verified is not None and total is not None and verified > total:
            raise ValueError(f"{mode}: more verified claims than total claims")
        criteria = row["criteria"]
        if len(criteria) != 4 or any(item not in (None, 0, 1) for item in criteria):
            raise ValueError(f"{mode}: expected four binary criteria")
        result[mode] = {
            "seconds": seconds, "human_edits": edits,
            "verified_fraction": round(verified / total, 3) if verified is not None and total else None,
            "criteria_passed": sum(criteria) if None not in criteria else None,
        }
    result["agent_minus_chat"] = {key: round(result["agent"][key] - result["chat"][key], 3)
                                 if None not in (result["agent"][key], result["chat"][key]) else None
                                 for key in result["agent"]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.observations.read_text(encoding="utf-8")))
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f"ERROR: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
