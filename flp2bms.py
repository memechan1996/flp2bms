"""FLP -> BMS (automation無視 / 音切り)。

使い方: python flp2bms.py song.flp [-o OUTDIR] [--no-render] [--fl FL64.exe] [--tail SEC]
既定の出力先は、このスクリプトと同じ場所の export ディレクトリ。
VSTi等のチャンネルは FL Studio 本体(FL64.exe /R)でレンダリングする。FLは閉じておくこと。
"""
import argparse
import sys
from pathlib import Path

import fl_render
from bms_writer import write_bms
from flp_reader import read_flp
from slicer import slice_all

DEFAULT_OUT = Path(__file__).resolve().parent / "export"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FLPをautomation無視で音切りしてBMSに変換")
    ap.add_argument("flp", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--bpm", type=float, help="BPMを上書き")
    ap.add_argument("--no-render", action="store_true", help="VSTi等のチャンネルをレンダせず無視する")
    ap.add_argument("--fl", type=Path, help="FL64.exe のパス(既定はレジストリから検出)")
    ap.add_argument("--tail", type=float, default=2.0, help="VSTiの音に含める残響の長さ(秒)")
    args = ap.parse_args(argv)

    proj = read_flp(args.flp)
    render_chs = [ch for ch in proj.channels.values() if ch.kind == "render"]
    rendered = {}
    if render_chs and any(proj.channels[n.channel].kind == "render" for n in proj.notes):
        fl_exe = args.fl or fl_render.find_fl()
        reason = "--no-render 指定" if args.no_render else None if fl_exe else "FL Studioが見つかりません"
        if reason is None:
            try:  # --bpm で上書きする前に、FLPのテンポのままレンダする
                rendered = fl_render.render_project(proj, args.flp.read_bytes(), fl_exe, args.tail)
            except RuntimeError as e:
                print(f"エラー: {e}(VSTiを無視するなら --no-render)", file=sys.stderr)
                return 1
        else:
            names = ", ".join(ch.name for ch in render_chs)
            proj.warnings.append(f"VSTi等のチャンネルは無視しました({reason}): {names}")
            proj.notes = [n for n in proj.notes if proj.channels[n.channel].kind != "render"]
    if args.bpm:
        proj.bpm = args.bpm
    for w in proj.warnings:
        print(f"警告: {w}", file=sys.stderr)
    if not proj.notes:
        print("出力できるノートがありません", file=sys.stderr)
        return 1

    args.outdir.mkdir(parents=True, exist_ok=True)
    slices = slice_all(proj, args.outdir, rendered)
    bms = args.outdir / (args.flp.stem + ".bms")
    write_bms(proj, slices, bms)
    print(f"{bms} ({len(proj.notes)} notes, {len(slices)} wavs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
