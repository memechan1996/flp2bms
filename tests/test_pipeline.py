import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fl_render
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

    class Item(SimpleNamespace):  # pyflpのItemModel同様、生フィールドは[]で引ける
        def __getitem__(self, k):
            return getattr(self, k)

    note = Item(rack_channel=1, position=0, length=96, key=60, velocity=100)
    auto_note = Item(rack_channel=2, position=0, length=96, key=60, velocity=100)
    pat = SimpleNamespace(length=384, notes=[note, auto_note])
    item = Item(item_flags=0x40, pattern=pat, position=384, length=384, start_offset=float("nan"))
    muted_item = Item(item_flags=0x2040, pattern=pat, position=0, length=384, start_offset=-1.0)
    audio_item = Item(item_flags=0x40, channel=FakeAutomation(), position=0, length=384, start_offset=-1.0)
    track = type("T", (list,), {"enabled": True})([item, muted_item, audio_item])
    flp = SimpleNamespace(
        title="x", tempo=120.0, ppq=96,
        channels=[sampler, FakeAutomation()],
        arrangements=[SimpleNamespace(tracks=[track])],
    )
    monkeypatch.setattr(flp_reader.pyflp, "parse", lambda p: flp)
    flp_path = tmp_path / "x.flp"
    flp_path.write_bytes(b"FLhd" + (6).to_bytes(4, "little") + bytes(6) + b"FLdt" + bytes(4))
    proj = flp_reader.read_flp(flp_path)
    assert list(proj.channels) == [1]
    assert [n.tick for n in proj.notes] == [384]  # automation側ノート/clip、ミュートclipは含まれない
    assert any("Audio/Automation" in w for w in proj.warnings)


REAL_FLP = Path(__file__).resolve().parent.parent / "res" / "Project_1.flp"


@pytest.mark.skipif(not REAL_FLP.is_file(), reason="res/Project_1.flp がない")
def test_real_flp_fl25():
    """FL 25.2.4 で保存した実FLP(808 Kick/Snare、2パターン×4小節、130BPM)。"""
    proj = flp_reader.read_flp(REAL_FLP)
    assert proj.bpm == 130 and proj.ppq == 96
    by_ch = {}
    for n in proj.notes:
        by_ch.setdefault(proj.channels[n.channel].name, []).append(n.tick)
    assert by_ch["808 Kick"] == list(range(0, 3072, 96))
    assert by_ch["808 Snare"] == list(range(1536 + 48, 3072, 96))
    assert all(n.length == 0 and n.key == 60 for n in proj.notes)


REAL_FLP2 = REAL_FLP.with_name("Project_2.flp")


@pytest.mark.skipif(not REAL_FLP2.is_file(), reason="res/Project_2.flp がない")
def test_real_flp_fl25_with_vsti():
    """Project_1 + パターン3(4小節、Kick/Snare に加えて Vital/FLEX Bass のノート)。"""
    proj = flp_reader.read_flp(REAL_FLP2)
    by_ch = {}
    for n in proj.notes:
        by_ch.setdefault(proj.channels[n.channel].name, []).append(n.tick)
    assert by_ch["808 Kick"] == list(range(0, 4608, 96))
    assert by_ch["808 Snare"] == list(range(1536 + 48, 4608, 96))
    assert len(by_ch["FLEX Bass"]) == 8 and len(by_ch["Vital"]) == 12
    assert {ch.name: ch.kind for ch in proj.channels.values()}["Vital"] == "render"
    assert all(t >= 3072 for t in by_ch["Vital"] + by_ch["FLEX Bass"])  # パターン3のみ


def _render_setup():
    proj = flp_reader.read_flp(REAL_FLP2)
    notes = sorted((n for n in proj.notes if proj.channels[n.channel].kind == "render"),
                   key=lambda n: (n.channel, n.key, n.length, n.velocity))
    return proj, fl_render.layout(notes, proj.ppq, proj.bpm, 2.0)


@pytest.mark.skipif(not REAL_FLP2.is_file(), reason="res/Project_2.flp がない")
def test_build_render_flp(tmp_path):
    proj, slots = _render_setup()
    raw = REAL_FLP2.read_bytes()
    out = fl_render.build_render_flp(raw, slots)
    # FLに渡すファイルは3バイトDWORDイベントを元の形のまま保つ(補正するとFLが読めない)
    assert any(short for _, _, short in flp_reader.iter_events(out))
    p = tmp_path / "render.flp"
    p.write_bytes(out)
    back = flp_reader.read_flp(p)
    assert [(n.tick, n.channel, n.key) for n in back.notes] == [(s.tick, s.note.channel, s.note.key) for s in slots]
    assert len(slots) == 13 and slots[0].tick == proj.ppq * 4  # 先頭1小節は空ける


def test_cut_slots():
    ppq, bpm, sr = 96, 120.0, 1000  # 1tick = 1/192秒
    note = Note(tick=0, length=96, key=60, velocity=100, channel=1)
    slots = [fl_render.Slot(note, 192, 192)]  # 1秒目から最大1秒
    audio = np.zeros(4000, dtype=np.float32)
    audio[1000:1300] = 0.5  # 音は0.3秒、その後は無音
    audio[2500] = 0.5  # スロット外の音は含めない
    seg = fl_render.cut_slots(audio, sr, slots, ppq, bpm)[(1, 60, 96, 100)]
    assert len(seg) == 300 and seg[0] == 0.5


@pytest.mark.skipif(not (REAL_FLP2.is_file() and os.environ.get("FLP2BMS_FL_TEST")),
                    reason="FL Studioでのレンダは FLP2BMS_FL_TEST=1 のときだけ(FLを閉じておく)")
def test_render_with_fl():
    proj, slots = _render_setup()
    got = fl_render.render_project(proj, REAL_FLP2.read_bytes(), fl_render.find_fl(), 2.0)
    assert len(got) == 13
    for audio, sr in got.values():
        level = np.abs(audio).max(axis=1)
        assert level.max() > 0.1  # 無音でない
        assert np.argmax(level > 1e-3) < sr * 0.005  # 頭が遅れていない
