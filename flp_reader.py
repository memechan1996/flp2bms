"""pyflpでFLPを読み、Projectに変換する。

automation(Automationチャンネル、パターン内controllers、テンポ変化など)は読まない。
"""
from pathlib import Path

import pyflp
from pyflp.channel import Sampler

from models import Channel, Note, Project

_NOTE_NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _key_to_midi(key) -> int:
    """pyflpのNote.keyはint または 'C5' / 'C#5' 形式の文字列。FLのC5を60とする。"""
    if isinstance(key, int):
        return key
    s = str(key)
    semitone = _NOTE_NAMES[s[0]]
    i = 1
    if s[i] == "#":
        semitone += 1
        i += 1
    return semitone + 12 * (int(s[i:]) + 1)


def _resolve_sample(raw, flp_dir: Path) -> Path | None:
    if not raw:
        return None
    p = Path(str(raw))
    for cand in (p, flp_dir / p, flp_dir / p.name):
        if cand.is_file():
            return cand.resolve()
    return None


def _pattern_length(pattern) -> int:
    if pattern.length:
        return int(pattern.length)
    return max((int(n.position) + int(n.length) for n in pattern.notes), default=0)


def read_flp(path: str | Path) -> Project:
    path = Path(path)
    flp = pyflp.parse(path)
    proj = Project(title=flp.title or path.stem, bpm=float(flp.tempo), ppq=int(flp.ppq))

    for ch in flp.channels:
        if not isinstance(ch, Sampler):  # Automation / Instrument(VSTi等) / Layer は対象外
            continue
        sample = _resolve_sample(ch.sample_path, path.parent)
        if sample is None:
            proj.warnings.append(f"サンプルが見つかりません: {ch.name or ch.iid} ({ch.sample_path})")
            continue
        proj.channels[int(ch.iid)] = Channel(int(ch.iid), ch.name or ch.display_name or str(ch.iid), str(sample))

    arrangements = list(flp.arrangements)
    if not arrangements:
        proj.warnings.append("Arrangementがありません")
        return proj
    arr = arrangements[0]

    skipped_audio = False
    for track in arr.tracks:
        if not track.enabled:
            continue
        for item in track:
            if item.muted:
                continue
            if not hasattr(item, "pattern"):  # ChannelPLItem(Audio clip / Automation clip)
                skipped_audio = True
                continue
            pat = item.pattern
            plen = _pattern_length(pat)
            if plen <= 0:
                continue
            start_off = int(item.offsets[0]) if item.offsets else 0
            clip_len = int(item.length)
            pos0 = int(item.position)
            # クリップがパターンより長ければループ再生される
            for rep in range(start_off // plen, (start_off + clip_len - 1) // plen + 1):
                for n in pat.notes:
                    if int(n.group) not in proj.channels:
                        continue
                    rel = rep * plen + int(n.position) - start_off
                    if rel < 0 or rel >= clip_len:
                        continue
                    proj.notes.append(Note(
                        tick=pos0 + rel,
                        length=int(n.length),
                        key=_key_to_midi(n.key),
                        velocity=int(n.velocity),
                        channel=int(n.group),
                    ))
    if skipped_audio:
        proj.warnings.append("プレイリスト上のAudio/Automation clipは無視しました")
    proj.notes.sort(key=lambda n: (n.tick, n.channel, n.key))
    return proj
