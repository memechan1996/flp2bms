"""FLP -> BMS (automation無視 / 音切り)。

使い方: python flp2bms.py song.flp [-o OUTDIR]
既定の出力先は、このスクリプトと同じ場所の export ディレクトリ。
"""
import argparse
import sys
from pathlib import Path

from bms_writer import write_bms
from flp_reader import read_flp
from slicer import slice_all

DEFAULT_OUT = Path(__file__).resolve().parent / "export"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FLPをautomation無視で音切りしてBMSに変換")
    ap.add_argument("flp", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--bpm", type=float, help="BPMを上書き")
    args = ap.parse_args(argv)

    proj = read_flp(args.flp)
    if args.bpm:
        proj.bpm = args.bpm
    for w in proj.warnings:
        print(f"警告: {w}", file=sys.stderr)
    if not proj.notes:
        print("出力できるノートがありません", file=sys.stderr)
        return 1

    args.outdir.mkdir(parents=True, exist_ok=True)
    slices = slice_all(proj, args.outdir)
    bms = args.outdir / (args.flp.stem + ".bms")
    write_bms(proj, slices, bms)
    print(f"{bms} ({len(proj.notes)} notes, {len(slices)} wavs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
