"""Synthetic ECG beats -> trained model -> ecg_cnn.tflite  (DEV / DEMO ONLY)

    python synth_ecg_model.py                 # data/synthetic_ecg/*.npz, models/ecg_cnn.tflite

Why this exists: MIT-BIH isn't reachable from every machine, and the app
needs a real, loadable ECG model to integrate tflite_flutter end to end.

What it is NOT: evidence of clinical performance. The generator encodes our
own idea of what a PVC/APC looks like, so the model learns that idea back;
high scores here are expected and meaningless for patients. The model card
says status "synthetic-dev"; verify_models.py fails a release with it and
triage_service only loads it when RAKSHA_ECG_ALLOW_SYNTHETIC=1, labelling
every result as synthetic. Replace with train_ecg_model.py (MIT-BIH) ASAP.

Beat model: sum of Gaussians (P, Q, R, S, T) per beat, placed on an RR
sequence and sampled at 360 Hz, then realistic nuisances: baseline wander,
50 Hz mains, EMG noise, gain/offset of a 12-bit ESP32 ADC, R-peak jitter.
Classes follow the MIT-BIH grouping used by train_ecg_model.py:
    normal (0):   sinus beats, incl. bundle-branch-like wide/notched QRS ('L','R')
    abnormal (1): PVC ('V')  - early, no P, wide QRS, discordant T
                  APC ('A')  - early, abnormal/absent P, normal QRS
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

import ecg_preprocess as ep
from tflite_fc_writer import run_fc_model, write_fc_model

BASE = Path(__file__).resolve().parent
DATA = BASE / "data" / "synthetic_ecg"
MODELS = BASE / "models"
FS = ep.FS_HZ
PRE = 99                                   # same R-peak offset as train_ecg_model.py
KINDS = ("normal", "bbb", "pvc", "apc")    # bbb is labelled normal, like MIT-BIH 'L'/'R'
LABEL = {"normal": 0, "bbb": 0, "pvc": 1, "apc": 1}


# --------------------------------------------------------------------------- #
# Generator
# --------------------------------------------------------------------------- #
def _waves(kind: str, rr: float, rng) -> list:
    """(centre_s, amplitude_mV, width_s) relative to the R peak."""
    j = lambda v, f=0.2: v * rng.uniform(1 - f, 1 + f)                  # noqa: E731
    qt = 0.40 * np.sqrt(rr)                                              # Bazett-like
    if kind in ("normal", "bbb"):
        w = [(-j(0.19, .15), j(0.12), j(0.025)),                         # P
             (-0.025, -j(0.10), j(0.010)),                               # Q
             (0.0, j(1.0), j(0.012)),                                    # R
             (0.028, -j(0.20), j(0.010)),                                # S
             (j(qt * 0.62, .1), j(0.30), j(0.05))]                       # T
        if kind == "bbb":                                                # wide, notched QRS
            w[2] = (0.0, j(0.8), j(0.022))
            w.insert(3, (j(0.045, .15), j(0.55), j(0.018)))
            w[4] = (0.075, -j(0.15), j(0.02))
        return w
    if kind == "pvc":
        sign = rng.choice([-1, 1])
        return [(0.0, sign * j(1.3), j(0.040, .25)),                     # wide bizarre QRS
                (j(0.07), -sign * j(0.45), j(0.030)),
                (j(qt * 0.72, .1), -sign * j(0.40), j(0.07))]            # discordant T
    # apc: premature, P abnormal (inverted / tiny / early) or hidden in prior T
    p_amp = rng.choice([-1, 1]) * rng.uniform(0.0, 0.08)
    return [(-j(0.13, .2), p_amp, j(0.02)),
            (-0.025, -j(0.10), j(0.010)),
            (0.0, j(0.95), j(0.012)),
            (0.028, -j(0.20), j(0.010)),
            (j(qt * 0.62, .1), j(0.28), j(0.05))]


def make_record(rng, n_beats: int = 60, abnormal_rate: float = 0.3):
    """One synthetic 'patient': continuous ADC signal + (R index, kind) list."""
    hr = rng.uniform(50, 120)
    rr0 = 60.0 / hr
    base_kind = "bbb" if rng.random() < 0.15 else "normal"
    kinds, rrs = [], []
    for _ in range(n_beats):
        k = rng.choice(["pvc", "apc"]) if rng.random() < abnormal_rate else base_kind
        rr = rr0 * rng.normal(1, 0.03)
        if k == "pvc":
            rr *= rng.uniform(0.55, 0.75)
        elif k == "apc":
            rr *= rng.uniform(0.65, 0.85)
        if kinds and kinds[-1] == "pvc":
            rr = rr0 * rng.uniform(1.2, 1.45)                             # compensatory pause
        kinds.append(k)
        rrs.append(rr)
    r_times = 1.0 + np.cumsum(rrs)
    n = int((r_times[-1] + 1.0) * FS)
    t = np.arange(n) / FS
    sig = np.zeros(n)
    for rt, k, rr in zip(r_times, kinds, rrs):
        lo, hi = int((rt - 0.5) * FS), int((rt + 0.7) * FS)
        seg = t[lo:hi] - rt
        for c, a, w in _waves(k, rr, rng):
            sig[lo:hi] += a * np.exp(-0.5 * ((seg - c) / w) ** 2)

    # nuisances
    sig *= rng.uniform(0.5, 1.6) * rng.choice([1, 1, 1, -1])              # lead gain/polarity
    for _ in range(2):
        sig += rng.uniform(0, 0.3) * np.sin(2 * np.pi * rng.uniform(0.05, 0.5) * t + rng.uniform(0, 6.3))
    sig += rng.uniform(0, 0.05) * np.sin(2 * np.pi * 50 * t)
    sig += rng.normal(0, rng.uniform(0.01, 0.06), n)
    adc = np.clip(2048 + sig * rng.uniform(300, 600) + rng.normal(0, 40), 0, 4095).round()
    return adc, [(int(round(rt * FS)), k) for rt, k in zip(r_times, kinds)]


def make_split(n_records: int, seed: int, noise_scale: float = 1.0):
    rng = np.random.default_rng(seed)
    X, y, kind, rec = [], [], [], []
    for r in range(n_records):
        adc, beats = make_record(rng)
        if noise_scale != 1.0:
            adc = np.clip(adc + rng.normal(0, 25 * noise_scale, adc.size), 0, 4095)
        for idx, k in beats[1:-1]:
            s = idx - PRE + int(rng.integers(-4, 5))                     # R-detector jitter
            if s < 0 or s + ep.WINDOW > adc.size:
                continue
            X.append(adc[s:s + ep.WINDOW]); y.append(LABEL[k]); kind.append(k); rec.append(r)
    return (np.asarray(X, np.float32), np.asarray(y, np.int64),
            np.asarray(kind), np.asarray(rec, np.int32))


# --------------------------------------------------------------------------- #
def metrics(y, p) -> dict:
    tp = int(((p == 1) & (y == 1)).sum()); fn = int(((p == 0) & (y == 1)).sum())
    tn = int(((p == 0) & (y == 0)).sum()); fp = int(((p == 1) & (y == 0)).sum())
    return {"n": int(len(y)), "tp": tp, "fn": fn, "tn": tn, "fp": fp,
            "sensitivity_abnormal": tp / max(tp + fn, 1),
            "specificity": tn / max(tn + fp, 1),
            "accuracy": (tp + tn) / max(len(y), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-records", type=int, default=500)
    ap.add_argument("--test-records", type=int, default=120)
    ap.add_argument("--dart-out", type=Path, default=None)
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()

    # different seeds = different synthetic "patients" in each split
    Xtr_raw, ytr, ktr, _ = make_split(args.train_records, seed=1)
    Xte_raw, yte, kte, _ = make_split(args.test_records, seed=2)
    Xhd_raw, yhd, khd, _ = make_split(args.test_records, seed=3, noise_scale=3.0)
    DATA.mkdir(parents=True, exist_ok=True)
    for name, (X, y, k) in {"train": (Xtr_raw, ytr, ktr), "test": (Xte_raw, yte, kte),
                            "test_noisy": (Xhd_raw, yhd, khd)}.items():
        np.savez_compressed(DATA / f"{name}.npz", adc=X.astype(np.int16), label=y, kind=k)
    print(f"beats: train={len(ytr)} test={len(yte)} noisy={len(yhd)} "
          f"abnormal={100 * ytr.mean():.0f}%")

    Xtr = ep.preprocess_batch(Xtr_raw)[:, 0, :]
    Xte = ep.preprocess_batch(Xte_raw)
    Xhd = ep.preprocess_batch(Xhd_raw)

    from sklearn.neural_network import MLPClassifier
    clf = MLPClassifier(hidden_layer_sizes=(32, 16), activation="relu", alpha=1e-3,
                        batch_size=256, learning_rate_init=1e-3, max_iter=200,
                        early_stopping=True, n_iter_no_change=8, random_state=42)
    clf.fit(Xtr, ytr)

    # sklearn binary MLP ends in ONE logistic unit z. Emit logits [0, z]:
    # softmax([0, z])[1] == sigmoid(z), so probabilities are unchanged.
    (W0, W1, W2), (b0, b1, b2) = clf.coefs_, clf.intercepts_
    W_out = np.vstack([np.zeros((1, W2.shape[0])), W2.T])
    b_out = np.array([0.0, b2[0]])
    layers = [(W0.T, b0, "relu"), (W1.T, b1, "relu"), (W_out, b_out, None)]
    blob = write_fc_model(layers, [1, 1, ep.WINDOW],
                          "raksha ecg_cnn SYNTHETIC-DEV MLP 256-32-16-2 (not clinically validated)")

    # 1) the .tflite reproduces sklearn exactly; 2) score the .tflite itself
    for X in (Xte, Xhd):
        lg = run_fc_model(blob, X)
        p_tfl = np.exp(lg[:, 1]) / np.exp(lg).sum(1)
        assert np.max(np.abs(p_tfl - clf.predict_proba(X[:, 0, :])[:, 1])) < 1e-4, "tflite != sklearn"
    m_te = metrics(yte, run_fc_model(blob, Xte).argmax(1))
    m_hd = metrics(yhd, run_fc_model(blob, Xhd).argmax(1))
    per_kind = {k: float((run_fc_model(blob, Xte[kte == k]).argmax(1) == LABEL[k]).mean())
                for k in KINDS if (kte == k).any()}

    MODELS.mkdir(exist_ok=True)
    out = MODELS / "ecg_cnn.tflite"
    out.write_bytes(blob)
    card = {
        "model": "ecg_cnn", "status": "synthetic-dev",
        "tflite": out.name, "sha256": hashlib.sha256(blob).hexdigest(),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "WARNING": "Trained only on synthetic beats. Not valid for patient use. "
                   "Replace with train_ecg_model.py (MIT-BIH) output.",
        "architecture": "MLP 256-32(relu)-16(relu)-2, TFLite FULLY_CONNECTED only",
        "dataset": {"generator": "synth_ecg_model.py (Gaussian P-QRS-T + noise)",
                    "train_beats": int(len(ytr)), "records": args.train_records,
                    "kinds": {k: int((ktr == k).sum()) for k in KINDS}},
        "input": {"shape": [1, 1, ep.WINDOW], "preprocess": "ecg_preprocess.preprocess",
                  "fs_hz": FS, "r_peak_offset": PRE},
        "output": ["logit_normal (always 0)", "logit_abnormal"],
        "test_metrics_on_tflite": m_te,
        "test_noisy_metrics_on_tflite": m_hd,
        "test_accuracy_by_kind": per_kind,
        "interpretation": "Scores measure how well the model learned the generator, nothing more.",
    }
    (MODELS / "ecg_cnn.model_card.json").write_text(json.dumps(card, indent=2))

    if args.dart_out:
        fx = args.dart_out / "test" / "fixtures"
        fx.mkdir(parents=True, exist_ok=True)
        idx = np.random.default_rng(0).choice(len(yte), 40, replace=False)
        (fx / "ecg_parity.json").write_text(json.dumps({
            "model_sha256": card["sha256"], "synthetic": True,
            "rows": [{"raw_adc": Xte_raw[i].tolist(), "input": Xte[i, 0].tolist(),
                      "logits": run_fc_model(blob, Xte[i:i + 1])[0].tolist(),
                      "label": int(yte[i])} for i in idx]}))

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 4, figsize=(14, 5), sharex=True)
        tt = (np.arange(ep.WINDOW) - PRE) / FS * 1000
        for c, k in enumerate(KINDS):
            sel = np.where(kte == k)[0][:1]
            if not sel.size:
                continue
            axes[0, c].plot(tt, Xte_raw[sel[0]], lw=.8); axes[0, c].set_title(f"{k} (raw ADC)")
            axes[1, c].plot(tt, Xte[sel[0], 0], lw=.8, color="C1"); axes[1, c].set_title("preprocessed")
            axes[1, c].set_xlabel("ms from R")
        fig.suptitle("Synthetic ECG beats (dev only)")
        fig.tight_layout()
        fig.savefig(DATA / "preview.png", dpi=110)

    print(json.dumps({"test": m_te, "test_noisy": m_hd, "by_kind": per_kind}, indent=1))
    print(f"wrote {out} ({len(blob):,} bytes)  STATUS: SYNTHETIC-DEV")
    return 0


if __name__ == "__main__":
    sys.exit(main())
