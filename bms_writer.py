"""Project + スライスwav対応表 からBMSテキストを生成する。小節長は4/4固定。"""
from math import gcd
from pathlib import Path

from models import Project
from slicer import note_key

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_WAV = 36 * 36 - 1


def to_id(i: int) -> str:
    return _B36[i // 36] + _B36[i % 36]


def _bpm_str(bpm: float) -> str:
    return str(int(bpm)) if float(bpm).is_integer() else f"{bpm:g}"


def build_bms(proj: Project, slices: dict[tuple, str]) -> str:
    files = list(slices.values())
    if len(files) > MAX_WAV:
        raise ValueError(f"スライス数が{MAX_WAV}を超えています({len(files)})")
    wav_id = {f: to_id(i + 1) for i, f in enumerate(files)}

    lines = ["#PLAYER 1", f"#TITLE {proj.title}", f"#BPM {_bpm_str(proj.bpm)}", ""]
    lines += [f"#WAV{wav_id[f]} {f}" for f in files]
    lines.append("")

    measure_ticks = proj.ppq * 4
    by_measure: dict[int, dict[int, list[str]]] = {}
    for n in proj.notes:
        m, pos = divmod(n.tick, measure_ticks)
        by_measure.setdefault(m, {}).setdefault(pos, []).append(wav_id[slices[note_key(n)]])

    for m in sorted(by_measure):
        positions = by_measure[m]
        div = measure_ticks
        for pos in positions:
            div = gcd(div, pos)
        res = measure_ticks // div  # 小節の分解能
        depth = max(len(v) for v in positions.values())
        for layer in range(depth):  # 同時発音は同一チャンネルを複数行に分けて表現
            slots = ["00"] * res
            for pos, ids in positions.items():
                if layer < len(ids):
                    slots[pos // div] = ids[layer]
            lines.append(f"#{m:03d}01:{''.join(slots)}")
    return "\n".join(lines) + "\n"


def write_bms(proj: Project, slices: dict[tuple, str], path: Path) -> None:
    path.write_text(build_bms(proj, slices), encoding="utf-8")
