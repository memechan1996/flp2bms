# flp2bms

FL Studioの `.flp` を、**automationを無視**して音切り(ノート単位でサンプルをキーサウンドwavに切り出し)した `.bms` に変換するPython製ツール。

## 構成
- `flp2bms.py` — CLI。`python flp2bms.py song.flp [-o OUTDIR] [--bpm N]`。出力先の既定は `export/`(`.bms`とwavを直下に出力)。
- `flp_reader.py` — pyflpでFLPを読み `Project` に変換。Samplerチャンネルのノートのみ使用。automation(Automationチャンネル、controllers、テンポ変化)は読まない。
- `slicer.py` — ノートごとにwav生成(サンプル先頭から再生、リサンプルでピッチ変更、ノート長で切断、velocityでゲイン)。同一(channel,key,length,velocity)は共有。
- `bms_writer.py` — BMS生成。4/4固定、ch01に配置、同時発音は同一チャンネルを複数行に分割。WAV IDは36進2桁(最大1295)。
- `models.py` — dataclass(Note/Channel/Project)。
- `tests/test_pipeline.py` — pytest。

## 環境
- Windows版Python 3.12をwingetで導入済み。依存は `.venv`(git管理外)に入れる:
  `python -m venv .venv` → `.venv\Scripts\python.exe -m pip install pyflp numpy soundfile scipy pytest`
- システムの `python` はMSYS2版でpipが使えないので使わない。
- テスト: `.venv\Scripts\python.exe -m pytest -q`

## 現在の状況 (2026-10-06)
- 初版実装済み、テスト3件パス(合成データとpyflpのダミーオブジェクトのみ)。
- **実FLPでは未検証**。`flp_reader.py` は実FLPが無い状態で書いたため、pyflpの属性差(Note.keyの型、`item.offsets`、sample_pathの解決等)で修正が要る可能性あり。
- 未対応: VSTi等のレンダリング、プレイリストのAudio clip(警告してスキップ)、4/4以外の拍子、テンポ変化、ノートごとのpan/fine_pitch、チャンネルvolume/pan/pitch_shift。

## TODO
1. 実FLPで動作確認(サンプルパス `%FLStudioFactoryData%` 等の解決含む)。
2. automationありとなしでBMS/wavが同一になることを確認、beatorajaなどで再生確認。
3. Audio clipのtick位置での切り出し対応を検討。
