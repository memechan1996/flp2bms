"""ノート単位でサンプルを切り出してwavを生成する。"""
from pathlib import Path

import numpy as np
import soundfile as sf

from models import Note, Project

ROOT_KEY = 60  # FLのC5
FADE_SEC = 0.005


def note_key(n: Note) -> tuple:
    return (n.channel, n.key, n.length, n.velocity)


def _resample(sample: np.ndarray, ratio: float, n: int) -> np.ndarray:
    """速度比ratioで再生した先頭nサンプルを線形補間で求める。"""
    if ratio == 1.0:
        return sample[:n].astype(np.float32)
    pos = np.arange(n) * ratio
    idx = np.arange(len(sample))
    if sample.ndim == 1:
        return np.interp(pos, idx, sample).astype(np.float32)
    return np.stack([np.interp(pos, idx, sample[:, c]) for c in range(sample.shape[1])], axis=1).astype(np.float32)


def fade_out(out: np.ndarray, sr: int) -> np.ndarray:
    """末尾に短いフェードをかけてクリックノイズを防ぐ(in-place)。"""
    fade = min(len(out), int(FADE_SEC * sr))
    if fade > 1:
        shape = (fade,) + (1,) * (out.ndim - 1)
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32).reshape(shape)
    return out


def render_slice(sample: np.ndarray, sr: int, note: Note, bpm: float, ppq: int) -> np.ndarray:
    ratio = 2.0 ** ((note.key - ROOT_KEY) / 12.0)  # 再生速度比(ピッチ=速度)
    n = int((len(sample) - 1) / ratio) + 1  # サンプル末尾まで
    if note.length > 0:  # length 0 はステップシーケンサのノート(ワンショット、最後まで鳴らす)
        length_sec = note.length / ppq * 60.0 / bpm
        n = min(n, max(1, int(round(length_sec * sr))))
    out = _resample(sample, ratio, n)
    fade_out(out, sr)
    out *= min(note.velocity, 128) / 128.0
    return out


def slice_all(proj: Project, outdir: Path, rendered: dict[tuple, tuple[np.ndarray, int]] | None = None) -> dict[tuple, str]:
    """ユニークなノートごとにwavを書き出し、{note_key: ファイル名} を返す。

    kind="render" のチャンネルは、FL Studioでレンダ済みの音(rendered[note_key])を書き出す。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    cache: dict[str, tuple[np.ndarray, int]] = {}
    result: dict[tuple, str] = {}
    for n in proj.notes:
        k = note_key(n)
        if k in result:
            continue
        ch = proj.channels[n.channel]
        if ch.kind == "render":
            audio, sr = rendered[k]
            out = fade_out(audio.copy(), sr)
        else:
            if ch.sample_path not in cache:
                data, sr = sf.read(ch.sample_path, dtype="float32", always_2d=False)
                cache[ch.sample_path] = (data, sr)
            data, sr = cache[ch.sample_path]
            out = render_slice(data, sr, n, proj.bpm, proj.ppq)
        name = f"s{len(result) + 1:04d}.wav"
        sf.write(str(outdir / name), out, sr, subtype="PCM_16")
        result[k] = name
    return result
