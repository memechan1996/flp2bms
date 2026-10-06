"""ノート単位でサンプルを切り出してwavを生成する。"""
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from fractions import Fraction

from models import Note, Project

ROOT_KEY = 60  # FLのC5
FADE_SEC = 0.005


def note_key(n: Note) -> tuple:
    return (n.channel, n.key, n.length, n.velocity)


def render_slice(sample: np.ndarray, sr: int, note: Note, bpm: float, ppq: int) -> np.ndarray:
    ratio = 2.0 ** ((note.key - ROOT_KEY) / 12.0)  # 再生速度比(ピッチ=速度)
    frac = Fraction(1.0 / ratio).limit_denominator(1000)
    out = resample_poly(sample, frac.numerator, frac.denominator, axis=0) if frac != 1 else sample.copy()
    length_sec = note.length / ppq * 60.0 / bpm
    n = min(len(out), max(1, int(round(length_sec * sr))))
    out = out[:n].astype(np.float32)
    fade = min(n, int(FADE_SEC * sr))
    if fade > 1:
        shape = (fade,) + (1,) * (out.ndim - 1)
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32).reshape(shape)
    out *= min(note.velocity, 128) / 128.0
    return out


def slice_all(proj: Project, outdir: Path) -> dict[tuple, str]:
    """ユニークなノートごとにwavを書き出し、{note_key: ファイル名} を返す。"""
    outdir.mkdir(parents=True, exist_ok=True)
    cache: dict[str, tuple[np.ndarray, int]] = {}
    result: dict[tuple, str] = {}
    for n in proj.notes:
        k = note_key(n)
        if k in result:
            continue
        ch = proj.channels[n.channel]
        if ch.sample_path not in cache:
            data, sr = sf.read(ch.sample_path, dtype="float32", always_2d=False)
            cache[ch.sample_path] = (data, sr)
        data, sr = cache[ch.sample_path]
        out = render_slice(data, sr, n, proj.bpm, proj.ppq)
        name = f"s{len(result) + 1:04d}.wav"
        sf.write(str(outdir / name), out, sr, subtype="PCM_16")
        result[k] = name
    return result
