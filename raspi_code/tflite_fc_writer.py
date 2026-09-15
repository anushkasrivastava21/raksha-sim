"""Write / execute TFLite models made of FULLY_CONNECTED layers - no TensorFlow.

Used where TensorFlow is unavailable. The schema encoding is the one already
verified in build_urine_rules_tflite.py. TFLite's FULLY_CONNECTED flattens any
input to [-1, in_features], so a (1, 1, 256) input feeds a 256-wide layer directly.

    blob = write_fc_model([(W1, b1, "relu"), (W2, b2, None)], input_shape=[1, 1, 256])
    y = run_fc_model(blob, x)        # independent decoder, for verification
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import flatbuffers
import numpy as np

from build_urine_rules_tflite import (  # noqa: F401  (re-used, verified encoders)
    ACT_NONE, ACT_RELU, FLOAT32, OP_FULLY_CONNECTED, OPTS_FULLY_CONNECTED,
    _T, _bytes, _ivec, _uoffvec,
)
import struct

Layer = Tuple[np.ndarray, np.ndarray, Optional[str]]


def write_fc_model(layers: Sequence[Layer], input_shape: List[int], description: str,
                   input_name: str = "input", output_name: str = "logits") -> bytes:
    b = flatbuffers.Builder(1 << 16)
    consts = []
    for W, bias, _ in layers:
        consts += [np.ascontiguousarray(W, "<f4"), np.ascontiguousarray(bias, "<f4")]
    bufs = []
    for arr in [None] + consts:
        data = None if arr is None else _bytes(b, arr.tobytes())
        b.StartObject(3)
        if data is not None:
            b.PrependUOffsetTRelativeSlot(0, data, 0)
        bufs.append(b.EndObject())

    # tensor list: input, then per layer [W, b, out]
    spec = [(input_name, list(input_shape), 0)]
    batch = int(np.prod(input_shape)) // int(layers[0][0].shape[1])
    for k, (W, bias, _) in enumerate(layers):
        last = k == len(layers) - 1
        spec += [(f"fc{k}/w", list(W.shape), 1 + 2 * k),
                 (f"fc{k}/b", list(bias.shape), 2 + 2 * k),
                 (output_name if last else f"fc{k}/out", [batch, int(W.shape[0])], 0)]
    tens = []
    for name, shape, buf in spec:
        n, s = b.CreateString(name), _ivec(b, shape)
        b.StartObject(8)
        b.PrependUOffsetTRelativeSlot(0, s, 0)
        b.PrependInt8Slot(1, FLOAT32, 0)
        b.PrependUint32Slot(2, buf, 0)
        b.PrependUOffsetTRelativeSlot(3, n, 0)
        tens.append(b.EndObject())

    ops = []
    prev = 0
    for k, (_, _, act) in enumerate(layers):
        w, bb, out = 1 + 3 * k, 2 + 3 * k, 3 + 3 * k
        b.StartObject(4)
        b.PrependInt8Slot(0, ACT_RELU if act == "relu" else ACT_NONE, 0)
        opts = b.EndObject()
        iv, ov = _ivec(b, [prev, w, bb]), _ivec(b, [out])
        b.StartObject(5)
        b.PrependUint32Slot(0, 0, 0)
        b.PrependUOffsetTRelativeSlot(1, iv, 0)
        b.PrependUOffsetTRelativeSlot(2, ov, 0)
        b.PrependUint8Slot(3, OPTS_FULLY_CONNECTED, 0)
        b.PrependUOffsetTRelativeSlot(4, opts, 0)
        ops.append(b.EndObject())
        prev = out

    tv, ov_ = _uoffvec(b, tens), _uoffvec(b, ops)
    gin, gout, gname = _ivec(b, [0]), _ivec(b, [prev]), b.CreateString("main")
    b.StartObject(5)
    b.PrependUOffsetTRelativeSlot(0, tv, 0)
    b.PrependUOffsetTRelativeSlot(1, gin, 0)
    b.PrependUOffsetTRelativeSlot(2, gout, 0)
    b.PrependUOffsetTRelativeSlot(3, ov_, 0)
    b.PrependUOffsetTRelativeSlot(4, gname, 0)
    sg = b.EndObject()

    b.StartObject(4)
    b.PrependInt8Slot(0, OP_FULLY_CONNECTED, 0)
    b.PrependInt32Slot(2, 1, 1)
    b.PrependInt32Slot(3, OP_FULLY_CONNECTED, 0)
    code = b.EndObject()

    codes, sgs, bv = _uoffvec(b, [code]), _uoffvec(b, [sg]), _uoffvec(b, bufs)
    desc = b.CreateString(description)
    b.StartObject(8)
    b.PrependUint32Slot(0, 3, 0)
    b.PrependUOffsetTRelativeSlot(1, codes, 0)
    b.PrependUOffsetTRelativeSlot(2, sgs, 0)
    b.PrependUOffsetTRelativeSlot(3, desc, 0)
    b.PrependUOffsetTRelativeSlot(4, bv, 0)
    b.Finish(b.EndObject(), file_identifier=b"TFL3")
    return bytes(b.Output())


def run_fc_model(blob: bytes, x: np.ndarray) -> np.ndarray:
    """Decode the flatbuffer from scratch and execute it (batch = leading dim of x)."""
    assert blob[4:8] == b"TFL3"
    model = _T(blob, struct.unpack_from("<I", blob, 0)[0])
    bufs, codes = model.tables(4), model.tables(1)
    assert bufs[0].vec(0)[0] == 0
    sg = model.tables(2)[0]
    consts = {}
    for i, t in enumerate(sg.tables(0)):
        n, s = bufs[t.scalar(2, "<I")].vec(0)
        if n:
            consts[i] = np.frombuffer(blob, "<f4", n // 4, s).reshape(t.ints(0))
    vals = {sg.ints(1)[0]: np.asarray(x, np.float32)}
    for op in sg.tables(3):
        assert codes[op.scalar(0, "<I")].scalar(3, "<i") == OP_FULLY_CONNECTED
        i, w, bi = op.ints(1)
        W = consts[w]
        y = vals[i].reshape(-1, W.shape[1]) @ W.T + consts[bi]
        if op.table(4).scalar(0, "<b") == ACT_RELU:
            y = np.maximum(y, 0)
        vals[op.ints(2)[0]] = y.astype(np.float32)
    return vals[sg.ints(2)[0]]
