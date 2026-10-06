# flp2bms

FL Studio のプロジェクト(`.flp`)を、**automation を無視して**音切りした BMS(`.bms` + キーサウンド wav)に変換する Python 製ツールです。

- Sampler チャンネルは、サンプルをノート単位で切り出して wav にします(ピッチ・長さ・velocity を反映)。
- VSTi や FL 純正シンセなどの Instrument チャンネルは、FL Studio 本体(`FL64.exe`)でユニークな音を1音ずつレンダリングしてから切り出します。
- Automation チャンネル、controllers、テンポ変化は読みません。

## 必要なもの

- Windows
- Python 3.14
- FL Studio(Instrument チャンネルをレンダリングする場合。FL 25.2.4 で動作確認)

## セットアップ

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install pyflp numpy soundfile pytest
```

scipy は使いません(Smart App Control で DLL がブロックされるため)。

## 使い方

```powershell
.venv\Scripts\python.exe flp2bms.py song.flp
```

`export/` に `song.bms` とキーサウンドの wav が出力されます。

| オプション | 説明 |
| --- | --- |
| `-o, --outdir DIR` | 出力先(既定: スクリプトと同じ場所の `export/`) |
| `--bpm N` | BPM を上書き |
| `--no-render` | VSTi 等のチャンネルをレンダリングせず無視する |
| `--fl PATH` | `FL64.exe` のパス(既定はレジストリから検出) |
| `--tail SEC` | VSTi の音に含める残響の長さ(秒、既定 2.0) |

> **注意:** Instrument チャンネルをレンダリングするときは FL Studio を閉じてください。起動中だと FL の CLI レンダが失敗するため、エラーで終了します。

## 出力の仕様

- 拍子は 4/4 固定、ノートはすべて BGM チャンネル(ch01)に配置します。同時発音は複数行に分割します。
- 同じ (チャンネル, キー, 長さ, velocity) のノートは同じ wav を共有します。
- WAV ID は 36 進 2 桁で、最大 1295 個です。
- Sampler の音はサンプル先頭から再生し、ノート長で切ります(長さ 0 のステップ入力は全長)。

## 制限事項

- 未対応: プレイリストの Audio clip(警告してスキップ)、4/4 以外の拍子、テンポ変化、Sampler のノートごとの pan / fine pitch、チャンネルの volume / pan / pitch shift。
- VSTi の音にはマスターの FX(リミッターなど)もかかります。
- tempo-sync の LFO などは、レンダ用の一時プロジェクト上の位置で鳴るため、曲中と位相が変わることがあります。

## ファイル構成

| ファイル | 内容 |
| --- | --- |
| `flp2bms.py` | CLI |
| `flp_reader.py` | pyflp で FLP を読み込む(pyflp 2.2.1 向けの FL 25 互換パッチを含む) |
| `fl_render.py` | Instrument チャンネルを FL Studio でレンダリング |
| `slicer.py` | ノートごとの wav 生成 |
| `bms_writer.py` | BMS 出力 |
| `models.py` | データモデル |
| `tests/` | pytest |

## テスト

```powershell
.venv\Scripts\python.exe -m pytest -q
```

FL Studio を使う結合テストは、環境変数 `FLP2BMS_FL_TEST=1` を設定したときだけ実行されます(FL は閉じておくこと)。
