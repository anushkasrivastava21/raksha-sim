"""Real triage inference for ml_engine.py. No FastAPI, no torch.

  vitals ----------------------------> exact XGBoost rules (triage_xgboost.json)
  urine_rgb -> urine_cnn.tflite / rules --^
  ecg_samples -> ecg_cnn.tflite (only if its model card says accepted)
  speech text -> lexicon symptom extractor --> tiered escalation
  vitals ----------------------------> safety override (forces Red)

Every number returned comes from one of those sources, and the response says
which one. Nothing is a constant.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Sequence

import numpy as np

from config import get_config
from export_triage_rules import load_rule_model

log = logging.getLogger("triage_service")
_CFG = get_config()
T, ECG, URINE = _CFG.triage, _CFG.ecg, _CFG.urine
TIER_ORDER = {"critical": 3, "moderate": 2, "minor": 1}


# --------------------------------------------------------------------------- #
# Model loading (once per process)
# --------------------------------------------------------------------------- #
def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def triage_model():
    return load_rule_model(T.model_path)


def _interpreter_cls():
    for mod in ("ai_edge_litert.interpreter", "tflite_runtime.interpreter",
                "tensorflow.lite.python.interpreter"):
        try:
            return __import__(mod, fromlist=["Interpreter"]).Interpreter
        except ImportError:
            continue
    return None


@dataclass
class _Lite:
    name: str
    status: str                       # accepted | rules-compiled
    sha: str
    interp: object = field(repr=False)

    def logits(self, x: np.ndarray) -> np.ndarray:
        i = self.interp.get_input_details()[0]
        o = self.interp.get_output_details()[0]
        self.interp.set_tensor(i["index"], x.astype(np.float32).reshape(i["shape"]))
        self.interp.invoke()
        return self.interp.get_tensor(o["index"])[0].copy()


@lru_cache(maxsize=None)
def _load_lite(path_str: str, allowed: tuple) -> Optional[_Lite]:
    from pathlib import Path
    path = Path(path_str)
    card_p = path.with_name(path.name.replace(".tflite", ".model_card.json"))
    if not (path.is_file() and card_p.is_file()):
        log.warning("%s: no model/card - not used", path.name)
        return None
    card = json.loads(card_p.read_text())
    sha = _sha(path)
    if card.get("status") not in allowed or card.get("sha256") != sha:
        log.error("%s: card status=%s or sha mismatch - refusing to load",
                  path.name, card.get("status"))
        return None
    cls = _interpreter_cls()
    if cls is None:
        log.warning("No TFLite runtime installed (pip install ai-edge-litert); %s unused", path.name)
        return None
    interp = cls(model_path=str(path))
    interp.allocate_tensors()
    return _Lite(path.stem, card["status"], sha, interp)


def ecg_model() -> Optional[_Lite]:
    return _load_lite(str(ECG.tflite_path), ("accepted",))          # never an untrained ECG


def urine_model() -> Optional[_Lite]:
    return _load_lite(str(URINE.tflite_path), ("accepted", "rules-compiled"))


# --------------------------------------------------------------------------- #
# Sub-assessments
# --------------------------------------------------------------------------- #
def assess_urine(rgb: Optional[Sequence[float]]) -> Dict:
    if not rgb or len(rgb) < 3:
        return {"severity": None, "source": "not measured"}
    r, g, b = (float(v) for v in rgb[:3])
    if max(r, g, b) > URINE.rgb_max:
        k = URINE.rgb_max / URINE.adc_max
        r, g, b = r * k, g * k, b * k
    m = urine_model()
    if m is not None:
        sev = int(np.argmax(m.logits(np.array([r, g, b]))))
        src = "ml-model" if m.status == "accepted" else "rules (compiled tflite)"
        return {"severity": sev, "source": src}
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    sev = int(luma < URINE.dark_amber_luma or r / max(g, 1e-6) > URINE.hematuria_red_ratio)
    return {"severity": sev, "source": "rules (python)"}


def assess_ecg(samples: Optional[Sequence[float]]) -> Dict:
    if not samples or len(samples) < ECG.min_samples:
        return {"result": ECG.label_undetermined, "source": "no ECG waveform"}
    m = ecg_model()
    if m is None:
        return {"result": ECG.label_undetermined, "source": "no accepted ECG model"}
    from ecg_preprocess import preprocess
    logits = m.logits(preprocess(samples))
    e = np.exp(logits - logits.max())
    p = e / e.sum()
    label = ECG.label_arrhythmia if p[1] > p[0] else ECG.label_normal
    return {"result": label, "source": "ml-model", "p_abnormal": round(float(p[1]), 3)}


def assess_symptoms(text: Optional[str]) -> List:
    if not text:
        return []
    try:
        import symptom_extractor
        return symptom_extractor.extract(text)
    except Exception as exc:                                   # noqa: BLE001
        log.error("symptom extraction failed: %s", exc)
        return []


def escalate(probs: np.ndarray, symptoms, arrhythmia: bool) -> np.ndarray:
    """Same rule as triage_integrator._escalate (kept torch-free here)."""
    probs = probs.astype(np.float64).copy()
    tier = max((s.tier for s in symptoms), key=lambda t: TIER_ORDER.get(t, 0), default=None)
    boost = {"critical": T.red_boost_critical, "moderate": T.red_boost_moderate,
             "minor": T.red_boost_minor}.get(tier or "", 0.0)
    if arrhythmia:
        boost = max(boost, T.arrhythmia_red_boost)
    boost = min(boost, T.red_boost_cap)
    if boost <= 0:
        return probs
    g, y, r = (T.class_labels.index(c) for c in ("Green", "Yellow", "Red"))
    probs[r] += boost
    probs[y] += T.symptom_yellow_boost
    probs[g] *= max(T.symptom_green_decay, 1.0 - boost / max(T.red_boost_cap, 1e-6))
    return probs / probs.sum()


def safety_flags(hr, spo2, temp, steth) -> List[str]:
    out = []
    if hr is not None and (hr < T.safety_hr_low or hr > T.safety_hr_high):
        out.append(f"Critical heart rate ({hr:g} BPM)")
    if spo2 is not None and spo2 < T.safety_spo2_low:
        out.append(f"Critical SpO2 ({spo2:g}%)")
    if temp is not None and (temp > T.safety_temp_high or temp < T.safety_temp_low):
        out.append(f"Abnormal temperature ({temp:g}°C)")
    if (steth or "").lower() == "abnormal":
        out.append("Abnormal auscultation sounds (clinician-reported)")
    return out


# --------------------------------------------------------------------------- #
def assess(*, patient_id: str, ecg_hr=None, bp_systolic=None, bp_diastolic=None,
           spo2=None, temperature=None, urine_rgb=None, ecg_samples=None,
           stethoscope_status=None, patient_speech_text=None) -> Dict:
    def val(x):
        return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)

    raw = {"ecg_hr": val(ecg_hr), "bp_sys": val(bp_systolic), "bp_dia": val(bp_diastolic),
           "spo2": val(spo2), "temperature": val(temperature)}
    defaults = {"ecg_hr": T.default_hr_bpm, "bp_sys": T.default_bp_sys,
                "bp_dia": T.default_bp_dia, "spo2": T.default_spo2,
                "temperature": T.default_temp_c}
    imputed = [k for k, v in raw.items() if v is None]
    feats = {k: (defaults[k] if v is None else v) for k, v in raw.items()}

    urine = assess_urine(urine_rgb)
    ecg = assess_ecg(ecg_samples)
    symptoms = assess_symptoms(patient_speech_text)
    if urine["severity"] is None:
        imputed.append("urine_severity")
    feats["urine_severity"] = float(urine["severity"] or 0)

    x = [feats[f] for f in T.feature_order]
    model_p = triage_model().predict_proba(x).astype(np.float64)
    arrhythmia = ecg["result"] == ECG.label_arrhythmia
    final_p = escalate(model_p, symptoms, arrhythmia)
    idx = int(np.argmax(final_p))
    decided_by = "model" if np.allclose(final_p, model_p) else "model+escalation"

    red = T.class_labels.index("Red")
    flags = safety_flags(raw["ecg_hr"], raw["spo2"], raw["temperature"], stethoscope_status)
    if flags and idx != red:
        idx, decided_by = red, "safety_override"

    measured = sum(raw[k] is not None for k in ("ecg_hr", "spo2", "temperature"))
    insufficient = measured < T.min_measured_vitals
    if insufficient and T.class_labels[idx] == "Green":
        idx, decided_by = T.class_labels.index("Yellow"), "insufficient_data"

    label = T.class_labels[idx]
    reasons = list(flags)
    if insufficient:
        reasons.append(f"Only {measured} of HR/SpO2/temperature measured - re-measure before clearing")
    reasons += [f"Reported symptom: {s.canonical} ({s.tier})" for s in symptoms]
    if arrhythmia:
        reasons.append("ECG model: abnormal beat morphology")
    if urine["severity"] == 1:
        reasons.append(f"Urine colour abnormal [{urine['source']}]")
    reasons.append(f"Vitals model: P(Green/Yellow/Red) = "
                   + "/".join(f"{p:.2f}" for p in model_p))
    if imputed:
        reasons.append("Not measured, population default used: " + ", ".join(imputed))

    ecg_m, uri_m = ecg_model(), urine_model()
    return {
        "patient_id": patient_id,
        "triage": label.upper(),
        "risk_score": round(float(final_p[red]), 2),
        # probability the pipeline assigns to the returned class; under a safety
        # override this can be low - that disagreement is information, not a bug
        "confidence": round(float(final_p[idx]), T.confidence_decimals),
        "is_abnormal": label != "Green",
        "decided_by": decided_by,
        "probabilities": {c: round(float(p), 4) for c, p in zip(T.class_labels, final_p)},
        "model_probabilities": {c: round(float(p), 4) for c, p in zip(T.class_labels, model_p)},
        "reasons": reasons,
        "ecg": ecg,
        "urine": urine,
        "symptoms": [s.canonical for s in symptoms],
        "imputed": imputed,
        "model_version": "xgb-{}|ecg-{}|urine-{}".format(
            triage_model().source_sha256[:8],
            ecg_m.sha[:8] if ecg_m else "none",
            (uri_m.sha[:8] + ("-rules" if uri_m.status != "accepted" else "")) if uri_m else "python-rules"),
    }
