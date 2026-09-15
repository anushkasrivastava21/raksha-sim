"""Release gate for Review 2: is every model the app ships real and traceable?

    python verify_models.py [--flutter-assets ../raksha-dash/assets/models]

Exit 0 only if:
  * ecg_cnn.tflite / urine_cnn.tflite exist, have a model card with
    status=accepted, and their sha256 matches the card (no silent swaps)
  * triage_rules.json was generated from the committed triage_xgboost.json
Urine may legitimately be absent (no labelled data yet); that is reported as
RULES-ONLY, which is allowed but must appear on the known-gaps slide.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ALLOW_SYNTHETIC = False


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_tflite(dirs, name: str, optional: bool) -> tuple[str, bool]:
    card_p = BASE / "models" / f"{name}.model_card.json"
    if not card_p.is_file():
        return (f"{name}: RULES-ONLY (no model card; needs labelled data)", True) if optional \
            else (f"{name}: MISSING - no model card, run the trainer", False)
    card = json.loads(card_p.read_text())
    rules_ok = optional and card.get("status") == "rules-compiled"
    if card.get("status") == "synthetic-dev":
        return (f"{name}: SYNTHETIC-DEV model - demo only, never a release", ALLOW_SYNTHETIC)
    if card.get("status") != "accepted" and not rules_ok:
        return f"{name}: REJECTED by its own gates - see {card_p.name}", False
    for d in dirs:
        f = d / card["tflite"]
        if not f.is_file():
            return f"{name}: {f} missing", False
        if sha(f) != card["sha256"]:
            return f"{name}: {f} sha256 differs from model card (stale or swapped file)", False
    if rules_ok:
        return f"{name}: RULES-COMPILED tflite (not trained; list as known gap)", True
    m = card["test_metrics_on_tflite"]
    return (f"{name}: OK sens={m['sensitivity_abnormal']:.3f} "
            f"spec={m['specificity']:.3f} n={m['n']}"), True


def check_triage() -> tuple[str, bool]:
    src, rules = BASE / "triage_xgboost.json", BASE / "models" / "triage_rules.json"
    if not (src.is_file() and rules.is_file()):
        return "triage: MISSING - run export_triage_rules.py", False
    if json.loads(rules.read_text())["source_model_sha256"] != sha(src):
        return "triage: rules are stale vs triage_xgboost.json - re-export", False
    return "triage: OK (exact rules, parity-checked at export)", True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--flutter-assets", type=Path, default=None)
    ap.add_argument("--allow-synthetic", action="store_true",
                    help="demo build: pass with a synthetic-dev ECG model")
    args = ap.parse_args()
    global ALLOW_SYNTHETIC
    ALLOW_SYNTHETIC = args.allow_synthetic
    dirs = [BASE / "models"] + ([args.flutter_assets] if args.flutter_assets else [])
    results = [check_tflite(dirs, "ecg_cnn", optional=False),
               check_tflite(dirs, "urine_cnn", optional=True),
               check_triage()]
    for msg, ok in results:
        print(("PASS  " if ok else "FAIL  ") + msg)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
