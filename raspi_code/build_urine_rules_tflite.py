"""Compile the documented urine colour rules into a real TFLite model.

No TensorFlow needed: the flatbuffer is written directly against the TFLite
schema (v3). The network is *constructed*, not trained:

    FC1 (RELU):  h0 = relu(110 - (0.299 r + 0.587 g + 0.114 b))   dark amber
                 h1 = relu(r - 1.35 g)                             red over green
    FC2:         logit_normal = 0 ; logit_abnormal = h0 + h1

argmax == urine_processor._rule_based_severity for every input (ties at
exactly 0 go to index 0 = normal, matching the rule's strict '<' / '>').

Same I/O contract as a trained urine_cnn: float32 (1,3) RGB 0-255 -> (1,2) logits.
When train_urine_model.py produces an ACCEPTED model it simply replaces this file.

    python build_urine_rules_tflite.py        -> models/urine_cnn.tflite + model card
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import struct
import sys
from pathlib import Path

import flatbuffers
import numpy as np

BASE = Path(__file__).resolve().parent
MODELS = BASE / "models"
DARK_AMBER_LUMA, HEMATURIA_RED_RATIO = 110.0, 1.35      # config.UrineConfig

# TFLite schema constants (schema.fbs v3)
FLOAT32 = 0
OP_FULLY_CONNECTED = 9
OPTS_FULLY_CONNECTED = 8
ACT_NONE, ACT_RELU = 0, 1

W1 = np.array([[-0.299, -0.587, -0.114], [1.0, -HEMATURIA_RED_RATIO, 0.0]], np.float32)
B1 = np.array([DARK_AMBER_LUMA, 0.0], np.float32)
W2 = np.array([[0.0, 0.0], [1.0, 1.0]], np.float32)
B2 = np.array([0.0, 0.0], np.float32)


# --------------------------------------------------------------------------- #
def _ivec(b: flatbuffers.Builder, xs) -> int:
    b.StartVector(4, len(xs), 4)
    for x in reversed(xs):
        b.PrependInt32(int(x))
    return b.EndVector()


def _uoffvec(b: flatbuffers.Builder, offs) -> int:
    b.StartVector(4, len(offs), 4)
    for o in reversed(offs):
        b.PrependUOffsetTRelative(o)
    return b.EndVector()


def _bytes(b: flatbuffers.Builder, data: bytes) -> int:
    b.StartVector(1, len(data), 16)                      # 16-byte aligned tensor data
    for byte in reversed(data):
        b.PrependUint8(byte)
    return b.EndVector()


def build() -> bytes:
    b = flatbuffers.Builder(1024)
    # buffers: 0 = mandatory empty sentinel, then one per constant tensor
    consts = [W1, B1, W2, B2]
    buf_offs = []
    for arr in [None] + consts:
        data = None if arr is None else _bytes(b, arr.astype("<f4").tobytes())
        b.StartObject(3)                                 # Buffer
        if data is not None:
            b.PrependUOffsetTRelativeSlot(0, data, 0)
        buf_offs.append(b.EndObject())

    # tensors: 0 in, 1 W1, 2 B1, 3 hidden, 4 W2, 5 B2, 6 out
    spec = [("rgb", [1, 3], 0), ("fc1/w", [2, 3], 1), ("fc1/b", [2], 2),
            ("hidden", [1, 2], 0), ("fc2/w", [2, 2], 3), ("fc2/b", [2], 4),
            ("logits", [1, 2], 0)]
    ten_offs = []
    for name, shape, buf in spec:
        n = b.CreateString(name)
        s = _ivec(b, shape)
        b.StartObject(8)                                 # Tensor
        b.PrependUOffsetTRelativeSlot(0, s, 0)           # shape
        b.PrependInt8Slot(1, FLOAT32, 0)                 # type
        b.PrependUint32Slot(2, buf, 0)                   # buffer
        b.PrependUOffsetTRelativeSlot(3, n, 0)           # name
        ten_offs.append(b.EndObject())

    op_offs = []
    for ins, out, act in (([0, 1, 2], 3, ACT_RELU), ([3, 4, 5], 6, ACT_NONE)):
        b.StartObject(4)                                 # FullyConnectedOptions
        b.PrependInt8Slot(0, act, 0)
        opts = b.EndObject()
        iv, ov = _ivec(b, ins), _ivec(b, [out])
        b.StartObject(5)                                 # Operator
        b.PrependUint32Slot(0, 0, 0)                     # opcode_index
        b.PrependUOffsetTRelativeSlot(1, iv, 0)
        b.PrependUOffsetTRelativeSlot(2, ov, 0)
        b.PrependUint8Slot(3, OPTS_FULLY_CONNECTED, 0)
        b.PrependUOffsetTRelativeSlot(4, opts, 0)
        op_offs.append(b.EndObject())

    tv, ops = _uoffvec(b, ten_offs), _uoffvec(b, op_offs)
    gin, gout, gname = _ivec(b, [0]), _ivec(b, [6]), b.CreateString("main")
    b.StartObject(5)                                     # SubGraph
    b.PrependUOffsetTRelativeSlot(0, tv, 0)
    b.PrependUOffsetTRelativeSlot(1, gin, 0)
    b.PrependUOffsetTRelativeSlot(2, gout, 0)
    b.PrependUOffsetTRelativeSlot(3, ops, 0)
    b.PrependUOffsetTRelativeSlot(4, gname, 0)
    sg = b.EndObject()

    b.StartObject(4)                                     # OperatorCode
    b.PrependInt8Slot(0, OP_FULLY_CONNECTED, 0)          # deprecated_builtin_code
    b.PrependInt32Slot(2, 1, 1)                          # version
    b.PrependInt32Slot(3, OP_FULLY_CONNECTED, 0)         # builtin_code
    opcode = b.EndObject()

    codes, sgs, bufs = _uoffvec(b, [opcode]), _uoffvec(b, [sg]), _uoffvec(b, buf_offs)
    desc = b.CreateString("raksha urine_cnn: documented colour rules compiled to FC layers (not trained)")
    b.StartObject(8)                                     # Model
    b.PrependUint32Slot(0, 3, 0)                         # schema version
    b.PrependUOffsetTRelativeSlot(1, codes, 0)
    b.PrependUOffsetTRelativeSlot(2, sgs, 0)
    b.PrependUOffsetTRelativeSlot(3, desc, 0)
    b.PrependUOffsetTRelativeSlot(4, bufs, 0)
    b.Finish(b.EndObject(), file_identifier=b"TFL3")
    return bytes(b.Output())


# --------------------------------------------------------------------------- #
# Independent reader: decodes the flatbuffer back and executes it.
# --------------------------------------------------------------------------- #
class _T:
    def __init__(self, buf: bytes, pos: int):
        self.buf, self.pos = buf, pos
        self.vt = pos - struct.unpack_from("<i", buf, pos)[0]
        self.vt_len = struct.unpack_from("<H", buf, self.vt)[0]

    def _off(self, fid):
        o = 4 + 2 * fid
        return struct.unpack_from("<H", self.buf, self.vt + o)[0] if o < self.vt_len else 0

    def scalar(self, fid, fmt, default=0):
        o = self._off(fid)
        return struct.unpack_from(fmt, self.buf, self.pos + o)[0] if o else default

    def _ref(self, fid):
        o = self._off(fid)
        if not o:
            return None
        p = self.pos + o
        return p + struct.unpack_from("<I", self.buf, p)[0]

    def vec(self, fid):
        p = self._ref(fid)
        if p is None:
            return 0, 0
        return struct.unpack_from("<I", self.buf, p)[0], p + 4

    def ints(self, fid):
        n, s = self.vec(fid)
        return list(struct.unpack_from(f"<{n}i", self.buf, s)) if n else []

    def tables(self, fid):
        n, s = self.vec(fid)
        return [_T(self.buf, s + 4 * i + struct.unpack_from("<I", self.buf, s + 4 * i)[0])
                for i in range(n)]

    def table(self, fid):
        p = self._ref(fid)
        return None if p is None else _T(self.buf, p)

    def string(self, fid):
        n, s = self.vec(fid)
        return self.buf[s:s + n].decode()


def run_reference(blob: bytes, x: np.ndarray) -> np.ndarray:
    assert blob[4:8] == b"TFL3", "missing TFL3 identifier"
    model = _T(blob, struct.unpack_from("<I", blob, 0)[0])
    assert model.scalar(0, "<I") == 3
    bufs = model.tables(4)
    assert bufs[0].vec(0)[0] == 0, "buffer 0 must be empty"
    codes = model.tables(1)
    sg = model.tables(2)[0]
    tensors = sg.tables(0)
    vals = {sg.ints(1)[0]: x.astype(np.float32)}
    for i, t in enumerate(tensors):
        n, s = bufs[t.scalar(2, "<I")].vec(0)
        if n:
            vals[i] = np.frombuffer(blob, "<f4", n // 4, s).reshape(t.ints(0))
    for op in sg.tables(3):
        code = codes[op.scalar(0, "<I")]
        assert code.scalar(3, "<i") == OP_FULLY_CONNECTED and op.scalar(3, "<B") == OPTS_FULLY_CONNECTED
        i, w, bias = op.ints(1)
        y = vals[i] @ vals[w].T + vals[bias]
        if op.table(4).scalar(0, "<b") == ACT_RELU:
            y = np.maximum(y, 0)
        vals[op.ints(2)[0]] = y.astype(np.float32)
    return vals[sg.ints(2)[0]]


def rule(r, g, b) -> int:
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return int(luma < DARK_AMBER_LUMA or r / max(g, 1e-6) > HEMATURIA_RED_RATIO)


def main() -> int:
    blob = build()
    rng = np.random.default_rng(0)
    X = np.vstack([rng.uniform(0, 255, (20000, 3)),
                   [[255, 234, 112], [200, 80, 70], [120, 90, 40], [0, 0, 0], [255, 255, 255]]])
    got = run_reference(blob, X).argmax(1)
    want = np.array([rule(*x) for x in X])
    # float32 vs float64 can only disagree within ~1e-4 of a decision boundary
    luma = X @ np.array([0.299, 0.587, 0.114])
    near = (np.abs(luma - DARK_AMBER_LUMA) < 1e-3) | (np.abs(X[:, 0] - HEMATURIA_RED_RATIO * X[:, 1]) < 1e-3)
    bad = (got != want) & ~near
    if bad.any():
        print(f"MISMATCH on {bad.sum()} rows, e.g. {X[bad][0]}")
        return 1
    MODELS.mkdir(exist_ok=True)
    out = MODELS / "urine_cnn.tflite"
    out.write_bytes(blob)
    card = {
        "model": "urine_cnn", "status": "rules-compiled",
        "tflite": out.name, "sha256": hashlib.sha256(blob).hexdigest(),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "what": "urine_processor._rule_based_severity expressed as FC layers; NOT trained",
        "input": {"shape": [1, 3], "units": "RGB 0-255 (scale ADC by 255/4095 first)"},
        "output": ["logit_normal", "logit_abnormal (= rule margin, not a probability)"],
        "verified": f"argmax == rule on {len(X)} inputs (reference decoder)",
        "replace_with": "train_urine_model.py once labelled data exists",
    }
    (MODELS / "urine_cnn.model_card.json").write_text(json.dumps(card, indent=2))
    print(f"wrote {out} ({len(blob)} bytes), agreement {100 * (got == want).mean():.3f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
