"""
Central configuration for Raksha-Sim.

Resolution order (highest priority first):
    1. Environment variable      RAKSHA_<SECTION>_<KEY>       e.g. RAKSHA_ECG_BANDPASS_HIGH_HZ=45
    2. JSON config file          path given by RAKSHA_CONFIG_FILE (default: ./raksha_config.json)
    3. Built-in defaults below

Nothing in the processing modules should contain a literal threshold, path,
model name or URL. Add it here instead.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ENV_PREFIX = "RAKSHA"

# --------------------------------------------------------------------------- #
# Layer 2: optional JSON overrides
# --------------------------------------------------------------------------- #

def _load_file_overrides() -> Mapping[str, Mapping[str, Any]]:
    path = Path(os.environ.get(f"{ENV_PREFIX}_CONFIG_FILE", BASE_DIR / "raksha_config.json"))
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            log.info("Loaded config overrides from %s", path)
            return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable config file %s: %s", path, exc)
    return {}


_FILE_OVERRIDES = _load_file_overrides()


def _resolve(section: str, key: str, default: Any, cast) -> Any:
    """Env var -> JSON file -> default, with a cast that never raises."""
    env_key = f"{ENV_PREFIX}_{section.upper()}_{key.upper()}"
    raw = os.environ.get(env_key)
    if raw is None:
        raw = _FILE_OVERRIDES.get(section, {}).get(key) if isinstance(_FILE_OVERRIDES, dict) else None
    if raw is None:
        return cast(default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        log.warning("Bad value for %s (%r); using default %r", env_key, raw, default)
        return cast(default)


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _as_path(v: Any) -> Path:
    p = Path(str(v)).expanduser()
    return p if p.is_absolute() else (BASE_DIR / p)


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ECGConfig:
    sample_rate_hz: float = _resolve("ecg", "sample_rate_hz", 360.0, float)
    bandpass_low_hz: float = _resolve("ecg", "bandpass_low_hz", 0.5, float)
    bandpass_high_hz: float = _resolve("ecg", "bandpass_high_hz", 45.0, float)
    filter_order: int = _resolve("ecg", "filter_order", 4, int)
    min_samples: int = _resolve("ecg", "min_samples", 10, int)
    # Adaptive pooling width -> the classifier accepts any input length.
    pooled_width: int = _resolve("ecg", "pooled_width", 78, int)
    conv_channels: int = _resolve("ecg", "conv_channels", 16, int)
    kernel_size: int = _resolve("ecg", "kernel_size", 5, int)
    stride: int = _resolve("ecg", "stride", 2, int)
    weights_path: Path = _resolve("ecg", "weights_path", "models/ecg_cnn.pt", _as_path)
    tflite_path: Path = _resolve("ecg", "tflite_path", "models/ecg_cnn.tflite", _as_path)
    # Demo builds only: permit a model card with status "synthetic-dev".
    allow_synthetic: bool = _resolve("ecg", "allow_synthetic", False, _as_bool)
    # Strip-level decision: arrhythmia if enough individual beats are abnormal.
    min_abnormal_beats: int = _resolve("ecg", "min_abnormal_beats", 2, int)
    min_abnormal_fraction: float = _resolve("ecg", "min_abnormal_fraction", 0.10, float)
    label_normal: str = _resolve("ecg", "label_normal", "Normal Sinus Rhythm", str)
    label_arrhythmia: str = _resolve("ecg", "label_arrhythmia", "Arrhythmia Detected", str)
    # Fail-safe: never claim "Normal" when the pipeline could not actually decide.
    label_undetermined: str = _resolve("ecg", "label_undetermined", "Undetermined", str)

    @property
    def nyquist_hz(self) -> float:
        return self.sample_rate_hz / 2.0


@dataclass(frozen=True)
class UrineConfig:
    adc_max: float = _resolve("urine", "adc_max", 4095.0, float)
    rgb_max: float = _resolve("urine", "rgb_max", 255.0, float)
    weights_path: Path = _resolve("urine", "weights_path", "models/urine_cnn.pt", _as_path)
    tflite_path: Path = _resolve("urine", "tflite_path", "models/urine_cnn.tflite", _as_path)
    # Deterministic colorimetric fallback used when no trained weights exist.
    dark_amber_luma: float = _resolve("urine", "dark_amber_luma", 110.0, float)
    hematuria_red_ratio: float = _resolve("urine", "hematuria_red_ratio", 1.35, float)
    severity_normal: int = _resolve("urine", "severity_normal", 0, int)
    severity_abnormal: int = _resolve("urine", "severity_abnormal", 1, int)
    default_rgb: tuple = (255.0, 234.0, 112.0)


@dataclass(frozen=True)
class AudioConfig:
    # --- ASR backend selection -------------------------------------------- #
    # "vosk"           : Kaldi, ~40 MB int8 models, official Android support   (default)
    # "whispercpp"     : GGML quantised Whisper via pywhispercpp
    # "faster_whisper" : CTranslate2 int8 Whisper
    # "transformers"   : original PyTorch path, dev machines only
    asr_backend: str = _resolve("audio", "asr_backend", "vosk", str)
    vosk_model_dir: Path = _resolve(
        "audio", "vosk_model_dir", "models/vosk-model-small-en-in-0.4", _as_path)
    vosk_min_word_confidence: float = _resolve("audio", "vosk_min_word_confidence", 0.45, float)
    whispercpp_model: Path = _resolve(
        "audio", "whispercpp_model", "models/ggml-tiny-q5_1.bin", _as_path)
    faster_whisper_model: str = _resolve("audio", "faster_whisper_model", "tiny", str)
    faster_whisper_compute: str = _resolve("audio", "faster_whisper_compute", "int8", str)
    asr_model: str = _resolve("audio", "asr_model", "openai/whisper-tiny", str)
    # --- Symptom extraction ------------------------------------------------ #
    # "lexicon" : gazetteer + fuzzy + negation, ~0 MB          (default on device)
    # "ner"     : original biomedical BERT, dev machines only
    symptom_backend: str = _resolve("audio", "symptom_backend", "lexicon", str)
    lexicon_file: Path = _resolve("audio", "lexicon_file", "data/symptom_lexicon.json", _as_path)
    fuzzy_enabled: bool = _resolve("audio", "fuzzy_enabled", True, _as_bool)
    fuzzy_threshold: int = _resolve("audio", "fuzzy_threshold", 88, int)
    fuzzy_max_ngram: int = _resolve("audio", "fuzzy_max_ngram", 3, int)
    # False = a negated *critical* symptom is retained (demoted) rather than dropped.
    negation_suppresses_critical: bool = _resolve(
        "audio", "negation_suppresses_critical", False, _as_bool)
    ner_model: str = _resolve("audio", "ner_model", "d4data/biomedical-ner-all", str)
    device: str = _resolve("audio", "device", "cpu", str)          # never "auto"
    torch_threads: int = _resolve("audio", "torch_threads", 2, int)
    target_sample_rate: int = _resolve("audio", "target_sample_rate", 16_000, int)
    record_seconds: float = _resolve("audio", "record_seconds", 5.0, float)
    record_channels: int = _resolve("audio", "record_channels", 1, int)
    record_dtype: str = _resolve("audio", "record_dtype", "int16", str)
    recording_path: Path = _resolve("audio", "recording_path", "recorded_audio.wav", _as_path)
    silence_threshold: float = _resolve("audio", "silence_threshold", 0.001, float)
    max_seconds: float = _resolve("audio", "max_seconds", 30.0, float)
    # Token budget: transcripts are truncated before they reach the NER model.
    max_transcript_chars: int = _resolve("audio", "max_transcript_chars", 512, int)
    asr_language: str = _resolve("audio", "asr_language", "english", str)
    asr_task: str = _resolve("audio", "asr_task", "transcribe", str)
    keyword_file: Path = _resolve("audio", "keyword_file", "data/clinical_keywords.json", _as_path)
    entity_tag: str = _resolve("audio", "entity_tag", "Sign_symptom", str)
    record_trigger: str = _resolve("audio", "record_trigger", "RECORD_MIC", str)


@dataclass(frozen=True)
class TriageConfig:
    model_path: Path = _resolve("triage", "model_path", "triage_xgboost.json", _as_path)
    feature_order: tuple = (
        "ecg_hr", "bp_sys", "bp_dia", "spo2", "temperature", "urine_severity",
    )
    class_labels: tuple = ("Green", "Yellow", "Red")
    # Prior used only when the booster cannot be loaded.
    fallback_probs: tuple = (0.80, 0.10, 0.10)
    # Clinical escalation applied on top of the model output.
    symptom_red_boost: float = _resolve("triage", "symptom_red_boost", 0.45, float)
    # Tiered escalation: a reported cough must not weigh the same as chest pain.
    red_boost_critical: float = _resolve("triage", "red_boost_critical", 0.45, float)
    red_boost_moderate: float = _resolve("triage", "red_boost_moderate", 0.18, float)
    red_boost_minor: float = _resolve("triage", "red_boost_minor", 0.05, float)
    red_boost_cap: float = _resolve("triage", "red_boost_cap", 0.60, float)
    symptom_yellow_boost: float = _resolve("triage", "symptom_yellow_boost", 0.10, float)
    symptom_green_decay: float = _resolve("triage", "symptom_green_decay", 0.10, float)
    arrhythmia_red_boost: float = _resolve("triage", "arrhythmia_red_boost", 0.45, float)
    # Safety override (MEWS-style): force Red regardless of the model.
    # These were the hardcoded "AI" thresholds in the old ml_engine.py.
    # Never report Green unless at least this many of HR/SpO2/temp were measured.
    min_measured_vitals: int = _resolve("triage", "min_measured_vitals", 2, int)
    safety_hr_low: float = _resolve("triage", "safety_hr_low", 40.0, float)
    safety_hr_high: float = _resolve("triage", "safety_hr_high", 130.0, float)
    safety_spo2_low: float = _resolve("triage", "safety_spo2_low", 90.0, float)
    safety_temp_low: float = _resolve("triage", "safety_temp_low", 35.0, float)
    safety_temp_high: float = _resolve("triage", "safety_temp_high", 39.0, float)
    confidence_decimals: int = _resolve("triage", "confidence_decimals", 2, int)
    # Vital defaults for fields the ESP32 rig does not yet transmit.
    default_hr_bpm: float = _resolve("triage", "default_hr_bpm", 75.0, float)
    default_spo2: float = _resolve("triage", "default_spo2", 98.0, float)
    default_bp_sys: float = _resolve("triage", "default_bp_sys", 120.0, float)
    default_bp_dia: float = _resolve("triage", "default_bp_dia", 80.0, float)
    default_temp_c: float = _resolve("triage", "default_temp_c", 37.0, float)


@dataclass(frozen=True)
class DatasetConfig:
    """Remote mock-payload source used by test_pipeline.py."""
    url: str = _resolve(
        "dataset", "url",
        "https://raw.githubusercontent.com/raksha-sim/mock-data/main/esp32_payloads.json",
        str,
    )
    timeout_s: float = _resolve("dataset", "timeout_s", 10.0, float)
    retries: int = _resolve("dataset", "retries", 3, int)
    backoff_s: float = _resolve("dataset", "backoff_s", 1.5, float)
    max_bytes: int = _resolve("dataset", "max_bytes", 5 * 1024 * 1024, int)
    cache_path: Path = _resolve("dataset", "cache_path", "data/.payload_cache.json", _as_path)
    use_cache: bool = _resolve("dataset", "use_cache", True, _as_bool)
    auth_token_env: str = _resolve("dataset", "auth_token_env", "RAKSHA_DATASET_TOKEN", str)


@dataclass(frozen=True)
class AppConfig:
    ecg: ECGConfig = field(default_factory=ECGConfig)
    urine: UrineConfig = field(default_factory=UrineConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    log_level: str = _resolve("app", "log_level", "INFO", str)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Single shared, immutable config instance."""
    cfg = AppConfig()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(levelname)s [%(name)s] %(message)s",
    )
    return cfg


if __name__ == "__main__":
    import dataclasses
    print(json.dumps(dataclasses.asdict(get_config()), indent=2, default=str))
