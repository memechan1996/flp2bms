# flp2bms

FL Studioの `.flp` を、**automationを無視**して音切り(ノート単位でサンプルをキーサウンドwavに切り出し)した `.bms` に変換するPython製ツール。

## 構成
- `flp2bms.py` — CLI。`python flp2bms.py song.flp [-o OUTDIR] [--bpm N] [--no-render] [--fl FL64.exe] [--tail SEC]`。出力先の既定は `export/`(`.bms`とwavを直下に出力)。
- `flp_reader.py` — pyflpでFLPを読み `Project` に変換。Samplerは`kind="sample"`、Instrument(VSTi/FL純正シンセ)は`kind="render"`。automation(Automationチャンネル、controllers、テンポ変化)は読まない。
  pyflp 2.2.1 への互換パッチを含む: Python 3.12+のEnum仕様変更、FL 25のID 172が3バイトで書かれる件(`iter_events`で境界判定、pyflpに渡す前だけ`normalize_flp`で4バイトに補正)、FL 25のプレイリストアイテム80バイト化、`%FLStudioFactoryData%` 等のマクロ展開(レジストリ参照)。
- `fl_render.py` — `kind="render"` のユニーク音を1音ずつ小節頭に並べた一時FLPを**元の生バイト列から**作り(パターン1つ・クリップ1つ、他パターンのノート/controllers・automation clipは除去、選択パターン(ID 67)を差し替え)、`FL64.exe /R /Ewav` で1回レンダして切り出す。
  - FLのCLIは選択中のパターンを書き出す。FL起動中に実行するとCLI側が落ちる(0xC0000602)ので事前チェックしてエラーにする。
  - FLに渡すファイルに`normalize_flp`済みのバイト列を使うと、FLが読めず空プロジェクトのまま待機し続ける。
- `slicer.py` — ノートごとにwav生成(サンプル先頭から再生、numpy線形補間でピッチ変更、ノート長で切断(length 0=ステップ入力は全長)、velocityでゲイン)。同一(channel,key,length,velocity)は共有。
- `bms_writer.py` — BMS生成。4/4固定、ch01に配置、同時発音は同一チャンネルを複数行に分割。WAV IDは36進2桁(最大1295)。
- `models.py` — dataclass(Note/Channel/Project)。
- `tests/test_pipeline.py` — pytest。

## 環境
- Python 3.14(`py -3.14`)。依存は `.venv`(git管理外)に入れる:
  `py -3.14 -m venv .venv` → `.venv\Scripts\python.exe -m pip install pyflp numpy soundfile pytest`
- scipyはSmart App ControlでDLLがブロックされるので使わない。
- テスト: `.venv\Scripts\python.exe -m pytest -q`

## 現在の状況 (2026-10-07)
- 実FLP `res/Project_1.flp`/`res/Project_2.flp`(FL 25.2.4、130BPM)で変換確認済み。Project_2のVital(VST2)/FLEX Bass(FL純正)はFLレンダで13音を生成、頭の遅れは1ms未満。
- テスト8件(FLを使う結合テストは `FLP2BMS_FL_TEST=1` のときだけ。FLを閉じて実行)。
- プレイリストアイテムのミュート判定は `item_flags & 0x2000` と推定(実データでは未確認。0x8000は非ミュートのクリップに立っていた)。
- VSTiの音にはマスターのFX(リミッター等)もかかる。tempo-syncのLFO等は一時FLP上の位置で鳴るため、曲中と位相が違うことがある。
- 未対応: プレイリストのAudio clip(警告してスキップ)、4/4以外の拍子、テンポ変化、Samplerのノートごとのpan/fine_pitch、チャンネルvolume/pan/pitch_shift。

## TODO
1. automationありとなしでBMS/wavが同一になることを確認、beatorajaなどで再生確認。
2. Audio clipのtick位置での切り出し対応を検討。
