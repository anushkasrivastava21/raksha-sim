"""Build every on-device model artefact from REAL sources, then gate the release.

Replaces the old converter, which exported randomly initialised Keras models
(train_dummy_models.py) and a surrogate of a dummy XGBoost booster.

    python convert_to_tflite.py [--dart-out ../raksha-dash]

  1. train_ecg_model.py    MIT-BIH, inter-patient split, gated  -> models/ecg_cnn.tflite
  2. train_urine_model.py  team-labelled CSV, gated (skips if no data)
  3. export_triage_rules.py exact XGBoost rules + parity check  -> models/triage_rules.json
  4. verify_models.py      fails the build if anything is fake, stale or rejected
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def run(*cmd: str) -> int:
    print("\n$", " ".join(cmd), flush=True)
    return subprocess.call([sys.executable, *cmd], cwd=BASE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dart-out", default=None, help="raksha-dash root for Dart code + fixtures")
    a = ap.parse_args()
    dart = ["--dart-fixture", a.dart_out] if a.dart_out else []
    run("train_ecg_model.py", *dart)
    if run("train_urine_model.py") != 0:                 # no data / rejected
        run("build_urine_rules_tflite.py")               # exact rules, labelled as such
    run("export_triage_rules.py", "--model", "triage_xgboost.json",
        *(["--dart-out", a.dart_out] if a.dart_out else []))
    if a.dart_out:
        run("ecg_preprocess.py", a.dart_out)
    return run("verify_models.py",
               *(["--flutter-assets", str(Path(a.dart_out) / "assets" / "models")] if a.dart_out else []))


if __name__ == "__main__":
    sys.exit(main())
