"""pyflpでFLPを読み、Projectに変換する。

automation(Automationチャンネル、パターン内controllers、テンポ変化など)は読まない。
"""
import enum
import math
import os
import re
import tempfile
import warnings
from collections import Counter
from pathlib import Path

import construct as c
import pyflp
from pyflp._events import ListEventBase, _EventEnumMeta
from pyflp.arrangement import PlaylistEvent
from pyflp.channel import Instrument, Sampler

from models import Channel, Note, Project


# Python 3.12+ では、メンバー0個のEnumに値を渡すと _missing_ を呼ぶ前に TypeError になる。
# pyflp 2.2.1 は EventEnum(id) で未知IDを受け付ける前提なので、値の検索として扱うよう戻す。
def _event_enum_call(cls, value, *args, **kwargs):
    if not args and not kwargs and not cls._member_map_:
        member = cls._missing_(value)
        if member is None:
            raise ValueError(f"{value!r} is not a valid {cls.__qualname__}")
        return member
    return enum.EnumMeta.__call__(cls, value, *args, **kwargs)


_EventEnumMeta.__call__ = _event_enum_call


# プレイリストアイテムは FL 20以前=32, FL 21〜=60, FL 25=80 バイト。pyflpは32/60のみ対応で、
# 160バイト(80x2)を32x5と誤認するため、_u1 が常に (120, 0) であることを手掛かりにサイズを決める。
_PL_ITEM_SIZES = (80, 60, 32)
_PL_U1 = bytes((120, 0))
PlaylistEvent.STRUCT = c.GreedyRange(
    c.Struct(
        "position" / c.Int32ul,
        "pattern_base" / c.Int16ul,
        "item_index" / c.Int16ul,
        "length" / c.Int32ul,
        "track_rvidx" / c.Int16ul,
        "group" / c.Int16ul,
        "_u1" / c.Bytes(2),
        "item_flags" / c.Int16ul,
        "_u2" / c.Bytes(4),
        "start_offset" / c.Float32l,
        "end_offset" / c.Float32l,
        "_u3" / c.Bytes(c.this._params.extra),
    )
)


def _playlist_event_init(self, id, data: bytes) -> None:
    size = next(
        (n for n in _PL_ITEM_SIZES
         if len(data) % n == 0 and all(data[i + 16 : i + 18] == _PL_U1 for i in range(0, len(data), n))),
        60 if len(data) % 60 == 0 else 32,
    )
    self.SIZES = [size]
    ListEventBase.__init__(self, id, data, extra=size - 32)


PlaylistEvent.__init__ = _playlist_event_init

_ITEM_MUTED = 0x2000  # item_flags のミュートビット

_EVENTS_START = 22  # FLhdチャンク(14) + "FLdt" + サイズ(4)
_SHORT_DWORD_IDS = (172,)  # FL 25 で値が3バイトしか書かれていないことがあるID


def _event_size(data: bytes, pos: int) -> int:
    """pos から始まるイベント全体(ID含む)のバイト数。"""
    eid = data[pos]
    if eid < 64:
        return 2
    if eid < 128:
        return 3
    if eid < 192:
        return 5
    p, size, shift = pos + 1, 0, 0
    while p < len(data):
        b = data[p]
        p += 1
        size |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            break
    return p - pos + size


def _walk(data: bytes, pos: int) -> tuple[int, int]:
    """posからイベントを規定サイズで読み進め、(終了位置, イベント数)を返す。"""
    count = 0
    while pos < len(data):
        pos += _event_size(data, pos)
        count += 1
    return pos, count


def iter_events(data: bytes):
    """生のFLPバイト列のイベントを (位置, バイト数, 3バイトDWORDか) で順に返す。

    FL 25 では一部のDWORDイベントが3バイトしか書かれておらず、規定どおり4バイトとして読むと
    以降のイベント境界がずれてゴミ(1バイトイベントの連続)になる。4バイト/3バイトのそれぞれで
    末尾まで読み、ファイル末尾にぴったり着地しかつイベント数が少ない方(ずれていない方)を採用する。
    """
    pos = _EVENTS_START
    while pos < len(data):
        if data[pos] in _SHORT_DWORD_IDS:
            end4, cnt4 = _walk(data, pos + 5)
            end3, cnt3 = _walk(data, pos + 4)
            if end3 == len(data) and (end4 != len(data) or cnt3 < cnt4):
                yield pos, 4, True
                pos += 4
                continue
        n = _event_size(data, pos)
        yield pos, n, False
        pos += n


def normalize_flp(data: bytes) -> bytes:
    """3バイトDWORDイベントを4バイトに補い、pyflpが正しく整列して読めるようにする。

    FL自身は元の形式で読むので、FLに渡すファイルには使わないこと。
    """
    out = bytearray(data[:_EVENTS_START])
    for pos, n, short in iter_events(data):
        out += data[pos : pos + n] + (bytes(1) if short else b"")
    out[18:22] = (len(out) - _EVENTS_START).to_bytes(4, "little")
    return bytes(out)


def _start_offset(item) -> int:
    """クリップ先頭のオフセット(tick)。未設定は -1 や NaN で保存されている。"""
    off = item["start_offset"]
    if off is None or not math.isfinite(off) or off < 0:
        return 0
    return int(off)


def _fl_shared_paths() -> dict[str, str]:
    """FL Studioのレジストリ(HKCU\\Software\\Image-Line\\Shared\\Paths)の値。取れなければ空。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Image-Line\Shared\Paths") as k:
            return {name: winreg.QueryValueEx(k, name)[0] for name in ("Install path", "Shared data")}
    except (ImportError, OSError):
        return {}


def _fl_macros() -> dict[str, str]:
    """サンプルパス中の %...% マクロの展開先。環境変数が設定されていればそちらを優先する。"""
    reg = _fl_shared_paths()
    install = reg.get("Install path")
    if not install:
        cands = sorted(Path(r"C:\Program Files\Image-Line").glob("FL Studio *"))
        install = str(cands[-1]) if cands else None
    macros = {}
    if install:
        macros["FLStudioFactoryData"] = install
        macros["FLPath"] = install
    if reg.get("Shared data"):
        macros["FLStudioUserData"] = str(Path(reg["Shared data"]) / "FL Studio")
    return macros


def _expand_macros(raw: str) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        return os.environ.get(name) or _fl_macros().get(name) or m.group(0)
    return re.sub(r"%([^%]+)%", sub, raw)


def _resolve_sample(raw, flp_dir: Path) -> Path | None:
    if not raw:
        return None
    p = Path(_expand_macros(str(raw)))
    for cand in (p, flp_dir / p, flp_dir / p.name):
        if cand.is_file():
            return cand.resolve()
    return None


def _pattern_length(pattern, ppq: int) -> int:
    """パターン長(tick)。未設定ならFL同様、最後のノートを含む小節(4/4)まで切り上げる。"""
    if pattern.length:
        return int(pattern.length)
    end = max((int(n.position) + max(int(n.length), 1) for n in pattern.notes), default=0)
    bar = ppq * 4
    return -(-end // bar) * bar


def read_flp(path: str | Path) -> Project:
    path = Path(path)
    data = path.read_bytes()
    fixed = normalize_flp(data)
    with warnings.catch_warnings():
        # VSTプラグインの状態データの未知マーカー。チャンネル/ノートの読み取りには影響しない
        warnings.filterwarnings("ignore", message="VSTPluginEvent: Unknown marker")
        if fixed == data:
            flp = pyflp.parse(path)
        else:  # pyflp.parse はパスしか受け付けない
            with tempfile.TemporaryDirectory() as tmp:
                tmp_flp = Path(tmp) / path.name
                tmp_flp.write_bytes(fixed)
                flp = pyflp.parse(tmp_flp)
    proj = Project(title=flp.title or path.stem, bpm=float(flp.tempo), ppq=int(flp.ppq))

    ch_names = {}
    for ch in flp.channels:
        ch_names[int(ch.iid)] = ch.name or ch.display_name or str(ch.iid)
        if isinstance(ch, Instrument):  # VSTi / FL純正シンセ(Samplerとは別クラス)
            proj.channels[int(ch.iid)] = Channel(int(ch.iid), ch_names[int(ch.iid)], kind="render")
            continue
        if not isinstance(ch, Sampler):  # Automation / Layer は対象外
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
    skipped_notes: Counter[int] = Counter()  # 対象外チャンネル(VSTi等)のノート数
    for track in arr.tracks:
        if not track.enabled:
            continue
        for item in track:
            if item["item_flags"] & _ITEM_MUTED:
                continue
            if not hasattr(item, "pattern"):  # ChannelPLItem(Audio clip / Automation clip)
                skipped_audio = True
                continue
            pat = item.pattern
            plen = _pattern_length(pat, proj.ppq)
            if plen <= 0:
                continue
            start_off = _start_offset(item)
            clip_len = int(item.length)
            pos0 = int(item.position)
            # クリップがパターンより長ければループ再生される
            for rep in range(start_off // plen, (start_off + clip_len - 1) // plen + 1):
                for n in pat.notes:
                    rel = rep * plen + int(n.position) - start_off
                    if rel < 0 or rel >= clip_len:
                        continue
                    if int(n.rack_channel) not in proj.channels:
                        skipped_notes[int(n.rack_channel)] += 1
                        continue
                    proj.notes.append(Note(
                        tick=pos0 + rel,
                        length=int(n.length),
                        key=int(n["key"]),  # FLのC5=60(n.keyは'C5'形式の文字列)
                        velocity=int(n.velocity),
                        channel=int(n.rack_channel),
                    ))
    if skipped_notes:
        detail = ", ".join(f"{ch_names.get(ch, ch)} {cnt}" for ch, cnt in sorted(skipped_notes.items()))
        proj.warnings.append(f"Automation/Layerまたはサンプル不明のチャンネルのノートは無視しました: {detail}")
    if skipped_audio:
        proj.warnings.append("プレイリスト上のAudio/Automation clipは無視しました")
    proj.notes.sort(key=lambda n: (n.tick, n.channel, n.key))
    return proj
