import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flp_reader
from bms_writer import build_bms
from flp2bms import DEFAULT_OUT
from models import Channel, Note, Project
from slicer import slice_all

SR = 44100


def make_sine(path, freq=440.0, sec=2.0):
    t = np.arange(int(SR * sec)) / SR
    sf.write(str(path), (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32), SR)


def peak_freq(path):
    d, sr = sf.read(str(path))
    spec = np.abs(np.fft.rfft(d * np.hanning(len(d))))
    return np.argmax(spec) * sr / len(d)


def test_slice_and_bms(tmp_path):
    wav = tmp_path / "a.wav"
    make_sine(wav)
    ppq = 96
    proj = Project("t", 120.0, ppq, {1: Channel(1, "a", str(wav))})
    # 120BPM: 1拍=0.5s。key60長さ1拍 / key72(1oct上)長さ1拍 / 同位置に同時発音 / 2小節目
    proj.notes = [
        Note(0, ppq, 60, 100, 1),
        Note(0, ppq, 72, 100, 1),
        Note(ppq * 2, ppq, 60, 100, 1),
        Note(ppq * 4, ppq, 60, 100, 1),
    ]
    out = tmp_path / "export"
    slices = slice_all(proj, out)
    assert len(slices) == 2  # 重複ノートは同じwavを共有
    f60 = out / slices[(1, 60, ppq, 100)]
    f72 = out / slices[(1, 72, ppq, 100)]
    assert abs(sf.info(str(f60)).duration - 0.5) < 0.01
    assert abs(peak_freq(f60) - 440) < 5
    assert abs(peak_freq(f72) - 880) < 10
    bms = build_bms(proj, slices)
    assert "#WAV01 s0001.wav" in bms and "#WAV02 s0002.wav" in bms
    assert "#00001:0101" in bms  # 小節0: 位置0と半小節目(res=2)
    assert "#00001:0200" in bms  # 同時発音は別行
    assert "#00101:01" in bms


def test_default_outdir_is_export():
    assert DEFAULT_OUT.name == "export"


def test_reader_ignores_automation(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    make_sine(wav)
    from pyflp.channel import Sampler

    sampler = Sampler.__new__(Sampler)  # コンストラクタを避けた最小モック
    attrs = dict(iid=1, name="s", display_name="s", sample_path=str(wav))
    monkeypatch.setattr(Sampler, "iid", property(lambda s: 1), raising=False)
    monkeypatch.setattr(Sampler, "name", property(lambda s: "s"), raising=False)
    monkeypatch.setattr(Sampler, "display_name", property(lambda s: "s"), raising=False)
    monkeypatch.setattr(Sampler, "sample_path", property(lambda s: str(wav)), raising=False)

    class FakeAutomation:  # Samplerではないチャンネル
        iid = 2
        name = "auto"

    note = SimpleNamespace(group=1, position=0, length=96, key=60, velocity=100)
    auto_note = SimpleNamespace(group=2, position=0, length=96, key=60, velocity=100)
    pat = SimpleNamespace(length=384, notes=[note, auto_note])
    item = SimpleNamespace(muted=False, pattern=pat, position=384, length=384, offsets=(0, 384))
    audio_item = SimpleNamespace(muted=False, channel=FakeAutomation(), position=0, length=384, offsets=None)
    track = type("T", (list,), {"enabled": True})([item, audio_item])
    flp = SimpleNamespace(
        title="x", tempo=120.0, ppq=96,
        channels=[sampler, FakeAutomation()],
        arrangements=[SimpleNamespace(tracks=[track])],
    )
    monkeypatch.setattr(flp_reader.pyflp, "parse", lambda p: flp)
    proj = flp_reader.read_flp(tmp_path / "x.flp")
    assert list(proj.channels) == [1]
    assert [n.tick for n in proj.notes] == [384]  # automation側ノート/clipは含まれない
    assert any("Audio/Automation" in w for w in proj.warnings)
