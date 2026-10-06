"""VSTi等(Sampler以外)のチャンネルのキー音を FL Studio 本体でレンダリングする。

ユニーク音((channel,key,length,velocity))ごとに、1音ずつ間隔を空けて1つのパターンに並べた
一時FLPを元FLPのバイト列から作り、`FL64.exe /R /Ewav` で1回だけ書き出してから切り出す。
automation(プレイリストのclip、パターン内controllers)は一時FLPから取り除く。
"""
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from flp_reader import _EVENTS_START, _fl_shared_paths, iter_events
from models import Note, Project

_PATTERN_NEW = 65
_PATTERN_SELECTED = 67  # PatternsID.CurrentlySelected(CLIレンダはこのパターンを書き出す)
_PATTERN_CONTROLLERS = 223
_PATTERN_NOTES = 224
_PLAYLIST = 233
_NOTE_SIZE = 24
_PL_ITEM_SIZE = 80
_PL_PATTERN_BASE = 20480
_PREROLL_BARS = 1  # 先頭の空白(プラグインの立ち上がり待ち)
SILENCE_DB = -60.0


@dataclass
class Slot:
    note: Note
    tick: int  # 一時FLP上の位置
    span: int  # 切り出す最大長(tick) = ノート長 + 残響の余白


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _events(data: bytes):
    """(ID, ペイロード, イベント全体の生バイト) を順に返す。"""
    for pos, n, _ in iter_events(data):
        eid = data[pos]
        if eid < 192:
            payload = data[pos + 1 : pos + n]
        else:
            p = pos + 1
            while data[p] & 0x80:
                p += 1
            payload = data[p + 1 : pos + n]
        yield eid, payload, data[pos : pos + n]


def _raw_note_key(raw: bytes) -> tuple:
    """生ノート(24バイト)から slicer.note_key と同じキーを作る。"""
    return (
        int.from_bytes(raw[6:8], "little"),  # rack_channel
        int.from_bytes(raw[12:14], "little"),  # key
        int.from_bytes(raw[8:12], "little"),  # length
        raw[21],  # velocity
    )


def layout(notes: list[Note], ppq: int, bpm: float, tail_sec: float) -> list[Slot]:
    """ユニーク音を小節頭から順に並べる。1音ごとに 音の長さ+tail を小節単位に切り上げた枠を取る。"""
    bar = ppq * 4
    tail = int(round(tail_sec * bpm / 60.0 * ppq))
    slots, tick, seen = [], _PREROLL_BARS * bar, set()
    for n in notes:
        k = (n.channel, n.key, n.length, n.velocity)
        if k in seen:
            continue
        seen.add(k)
        span = max(n.length, ppq // 4) + tail  # length 0(ステップ入力)は1ステップ分とみなす
        slots.append(Slot(n, tick, span))
        tick += -(-span // bar) * bar
    return slots


def build_render_flp(data: bytes, slots: list[Slot]) -> bytes:
    """slots の音だけが鳴る一時FLPを作る。data は元FLPの生バイト列(normalize_flp前のもの)。"""
    templates: dict[tuple, bytes] = {}
    first_pattern = None
    cur_pattern = None
    pl_template = None
    for eid, payload, _ in _events(data):
        if eid == _PATTERN_NEW:
            cur_pattern = int.from_bytes(payload, "little")
        elif eid == _PATTERN_NOTES:
            if first_pattern is None:
                first_pattern = cur_pattern
            for i in range(0, len(payload) - _NOTE_SIZE + 1, _NOTE_SIZE):
                raw = payload[i : i + _NOTE_SIZE]
                templates.setdefault(_raw_note_key(raw), raw)
        elif eid == _PLAYLIST and pl_template is None and len(payload) >= _PL_ITEM_SIZE:
            pl_template = payload[:_PL_ITEM_SIZE]
    if first_pattern is None:
        raise ValueError("ノートを持つパターンがありません")

    notes = bytearray()
    for s in slots:
        n = s.note
        raw = bytearray(templates[(n.channel, n.key, n.length, n.velocity)])
        raw[0:4] = s.tick.to_bytes(4, "little")
        notes += raw
    total = max(s.tick + s.span for s in slots) if slots else 0

    out = bytearray(data[:_EVENTS_START])
    cur_pattern = None
    for eid, payload, raw in _events(data):
        if eid == _PATTERN_NEW:
            cur_pattern = int.from_bytes(payload, "little")
        if eid == _PATTERN_CONTROLLERS:
            continue
        if eid == _PATTERN_NOTES:
            if cur_pattern != first_pattern:
                continue
            raw = bytes([eid]) + _varint(len(notes)) + notes
        elif eid == _PATTERN_SELECTED:
            raw = bytes([eid]) + first_pattern.to_bytes(2, "little")
        elif eid == _PLAYLIST:
            items = b""
            if pl_template is not None:
                item = bytearray(pl_template)
                item[0:4] = (0).to_bytes(4, "little")  # position
                item[4:6] = _PL_PATTERN_BASE.to_bytes(2, "little")
                item[6:8] = (_PL_PATTERN_BASE + first_pattern).to_bytes(2, "little")
                item[8:12] = total.to_bytes(4, "little")  # length
                item[12:14] = (499).to_bytes(2, "little")  # track_rvidx(トラック1)
                item[20:22] = (0x40).to_bytes(2, "little")  # item_flags(ミュートなし)
                items = bytes(item)
            raw = bytes([eid]) + _varint(len(items)) + items
        out += raw
    out[18:22] = (len(out) - _EVENTS_START).to_bytes(4, "little")
    return bytes(out)


def find_fl() -> Path | None:
    install = _fl_shared_paths().get("Install path")
    cands = [Path(install)] if install else []
    cands += sorted(Path(r"C:\Program Files\Image-Line").glob("FL Studio *"), reverse=True)
    for d in cands:
        exe = d / "FL64.exe"
        if exe.is_file():
            return exe
    return None


def fl_is_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "FL64.exe" in out


def render(flp_bytes: bytes, fl_exe: Path, timeout: float = 600) -> tuple[np.ndarray, int]:
    """FL64.exe /R /Ewav で一時FLPを書き出す。FLが起動中だとCLI側が落ちるので事前に弾く。"""
    if fl_is_running():
        raise RuntimeError("FL Studioが起動中です。閉じてから実行してください(起動中はCLIレンダが失敗します)")
    with tempfile.TemporaryDirectory() as tmp:
        flp = Path(tmp) / "flp2bms_render.flp"
        flp.write_bytes(flp_bytes)
        try:
            proc = subprocess.run([str(fl_exe), "/R", "/Ewav", str(flp)], timeout=timeout)
        except subprocess.TimeoutExpired:  # 読み込めないFLPだとFLが空のまま待機し続ける
            raise RuntimeError(f"FL Studioのレンダリングが{timeout:.0f}秒で終わりませんでした") from None
        wav = flp.with_suffix(".wav")
        if not wav.is_file():
            raise RuntimeError(f"FL Studioのレンダリングに失敗しました(終了コード {proc.returncode})")
        audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    return audio, sr


def render_project(proj: Project, flp_data: bytes, fl_exe: Path, tail_sec: float) -> dict[tuple, tuple[np.ndarray, int]]:
    """kind="render" のチャンネルのユニーク音を全部レンダし、{note_key: (音, sr)} を返す。"""
    notes = [n for n in proj.notes if proj.channels[n.channel].kind == "render"]
    if not notes:
        return {}
    notes.sort(key=lambda n: (n.channel, n.key, n.length, n.velocity))
    slots = layout(notes, proj.ppq, proj.bpm, tail_sec)
    audio, sr = render(build_render_flp(flp_data, slots), fl_exe)
    return {k: (a, sr) for k, a in cut_slots(audio, sr, slots, proj.ppq, proj.bpm).items()}


def cut_slots(audio: np.ndarray, sr: int, slots: list[Slot], ppq: int, bpm: float) -> dict[tuple, np.ndarray]:
    """各スロットを音の頭から span まで切り出し、末尾の無音を削る。"""
    sec_per_tick = 60.0 / bpm / ppq
    thresh = 10 ** (SILENCE_DB / 20)
    result = {}
    for s in slots:
        a = int(round(s.tick * sec_per_tick * sr))
        b = min(len(audio), int(round((s.tick + s.span) * sec_per_tick * sr)))
        seg = audio[a:b].astype(np.float32)
        level = np.abs(seg) if seg.ndim == 1 else np.abs(seg).max(axis=1)
        loud = np.nonzero(level >= thresh)[0]
        seg = seg[: loud[-1] + 1] if len(loud) else seg[:1]
        n = s.note
        result[(n.channel, n.key, n.length, n.velocity)] = seg
    return result
