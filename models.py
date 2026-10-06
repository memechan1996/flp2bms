"""FLPの内容を表す最小限のデータ構造。automationに関する情報は一切持たない。"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Note:
    tick: int  # 曲頭からの絶対位置(ticks)
    length: int  # ticks
    key: int  # MIDIノート番号 (FLのC5 = 60)
    velocity: int  # 0-128
    channel: int  # Channel.iid


@dataclass
class Channel:
    iid: int
    name: str
    sample_path: str  # 解決済みの絶対パス


@dataclass
class Project:
    title: str
    bpm: float
    ppq: int
    channels: dict[int, Channel] = field(default_factory=dict)
    notes: list[Note] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
