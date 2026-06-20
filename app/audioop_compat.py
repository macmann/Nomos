"""Small audioop compatibility subset for Python builds without audioop.

Python 3.13+ removed the stdlib audioop module. This module implements the
limited mono PCM/µ-law operations used by the Twilio audio pipeline.
"""

from __future__ import annotations

BIAS = 0x84
CLIP = 32635


def _sample_count(fragment: bytes, width: int) -> int:
    if width <= 0:
        raise ValueError("sample width must be positive")
    if len(fragment) % width:
        raise ValueError("not a whole number of frames")
    return len(fragment) // width


def _read_sample(fragment: bytes, index: int, width: int) -> int:
    start = index * width
    if width == 1:
        return fragment[start] - 128
    return int.from_bytes(fragment[start : start + width], "little", signed=True)


def _write_sample(value: int, width: int) -> bytes:
    if width == 1:
        return bytes((max(-128, min(127, value)) + 128,))
    bits = 8 * width
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    return int(max(minimum, min(maximum, value))).to_bytes(width, "little", signed=True)


def tomono(fragment: bytes, width: int, lfactor: float, rfactor: float) -> bytes:
    samples = _sample_count(fragment, width)
    if samples % 2:
        raise ValueError("not a whole number of stereo frames")
    out = bytearray()
    for i in range(0, samples, 2):
        left = _read_sample(fragment, i, width)
        right = _read_sample(fragment, i + 1, width)
        out.extend(_write_sample(round(left * lfactor + right * rfactor), width))
    return bytes(out)


def ratecv(
    fragment: bytes,
    width: int,
    nchannels: int,
    inrate: int,
    outrate: int,
    state,
    weightA=1,
    weightB=0,
):
    if nchannels != 1:
        raise ValueError("audioop_compat.ratecv only supports mono audio")
    if inrate <= 0 or outrate <= 0:
        raise ValueError("sampling rates must be positive")
    frames = _sample_count(fragment, width)
    if frames == 0 or inrate == outrate:
        return fragment, None

    input_samples = [_read_sample(fragment, i, width) for i in range(frames)]
    output_frames = max(1, round(frames * outrate / inrate))
    out = bytearray()
    for out_i in range(output_frames):
        src_pos = out_i * inrate / outrate
        lo = int(src_pos)
        hi = min(lo + 1, frames - 1)
        frac = src_pos - lo
        value = round(input_samples[lo] * (1 - frac) + input_samples[hi] * frac)
        out.extend(_write_sample(value, width))
    return bytes(out), None


def _linear_to_ulaw(sample: int) -> int:
    sample = max(-CLIP, min(CLIP, sample))
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample += BIAS
    exponent = 7
    exp_mask = 0x4000
    while exponent > 0 and not (sample & exp_mask):
        exponent -= 1
        exp_mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _ulaw_to_linear(byte: int) -> int:
    byte = (~byte) & 0xFF
    sign = byte & 0x80
    exponent = (byte >> 4) & 0x07
    mantissa = byte & 0x0F
    sample = ((mantissa << 3) + BIAS) << exponent
    sample -= BIAS
    return -sample if sign else sample


def lin2ulaw(fragment: bytes, width: int) -> bytes:
    return bytes(
        _linear_to_ulaw(_read_sample(fragment, i, width))
        for i in range(_sample_count(fragment, width))
    )


def ulaw2lin(fragment: bytes, width: int) -> bytes:
    out = bytearray()
    for byte in fragment:
        out.extend(_write_sample(_ulaw_to_linear(byte), width))
    return bytes(out)
