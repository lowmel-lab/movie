# テロップ・カット・音の設計

ヒルウラ代表が公開した[動画編集の手順](https://note.com/takatori88/n/n2ac06a7ca33c)を参考にし、CodexとFFmpegで動くように実装している。

## 文字起こしと実行

コマンドはリポジトリ直下から実行する。FFmpeg・ffprobe（字幕にはlibass）と、日本語用フォントを確認する。書体を `assets/fonts/` に置き、設計図の `font` にフォントファミリー名を指定できる。既定は `Noto Sans CJK JP`。

```sh
UV_CACHE_DIR="$PWD/.movie-cache/uv" uv venv .movie-cache/venv
UV_CACHE_DIR="$PWD/.movie-cache/uv" uv pip install --python .movie-cache/venv/bin/python -r scripts/requirements-asr.txt
.movie-cache/venv/bin/python scripts/transcribe.py --output output/案件/words.json -- input/01.mov input/02.mp4
python3 scripts/edit_story.py --words output/案件/words.json --plan output/案件/plan.json --output output/案件/初稿.mp4 --check
python3 scripts/edit_story.py --words output/案件/words.json --plan output/案件/plan.json --output output/案件/初稿.mp4
```

依存関係とモデルは初回のみ取得する。モデルは約163 MBで、音声認識は作業環境内で行う。取得できない環境では、既存のSenseVoiceモデルを `--model-dir` で指定できる。`--language ja` などで言語を固定できる。

## 設計図の作り方

文字起こしはクリップごとの `clips` に `path` と `words` があり、各区分に0始まりの `id`、`start`、`end`、`text` がある。日本語は文字単位に分かれることがあるため、単語の途中を境界にせず文節全体を選ぶ。書き出しで使う時刻は語番号から計算する。

- カットは語頭・語尾と意味を残し、長い間、余分なフィラー、言い直しを詰める。間を詰める効果が小さい場合は、両側の語を一つの区間で残す。映像で必要な間やBロールを優先する場合は `keep` を省略する。発話なしで間を残す場合は `keep_pauses: true`。
- 通常テロップは下、強調は中央に出す。発話に合わせ、文節や意味の切れ目で切り替える。横型は1行18字か2行で計24字、縦型は1行10字が目安。短い語尾だけを次のテロップに残さず、助詞だけで始まる区切りや固有名詞の分割を避ける。要約は強調や図解に使う。
- 強調は見せ場だけに絞る。手順や関係が発話に含まれるときは、2〜3個の短いラベルで図解を作る。強調と図解は同じ中央領域に重ねない。
- 効果音は、強調や図解が出るときだけ使う。明るい話題は `bright`、注意・落ち着いた転換は `down` を候補にし、30秒以内に同じ音を繰り返さない。話し声を中心にBGMを小さく敷く。元の音楽や環境音が重要なら `bgm: "off"` を使う。

次は書式例。`clips` の順序と本数を `words.json` に合わせ、番号を実際の発話に合わせる。`keep` を省略すると、文字起こしのある素材は全体を残す。各範囲は両端を含む。

```json
{
  "bgm": "auto",
  "clips": [{
    "keep": [[0, 20], [24, 50]],
    "captions": [{"words": [0, 9], "text": "最初のポイントを\n確認します"}],
    "highlights": [{"words": [12, 19], "text": "重要なポイント", "sound": "bright"}],
    "diagrams": [{"words": [30, 45], "labels": ["準備", "実行", "確認"]}]
  }]
}
```

手持ちの音源は `assets/bgm/default.mp3`、`assets/sfx/bright.wav`、`assets/sfx/down.wav` のように置く。音源がない場合は、このリポジトリで生成する曲と効果音を使う。声はおよそ-14 LUFS、BGMはおよそ-32 LUFSを基準にして、ミックス後のピークを抑える。

## トランジション

ジェットカットの細かいつなぎはテンポを優先し、トランジションは異なる素材や明確な場面の境界に使う。複数素材のつなぎは `--transition fade` で短い白フェードを使える。参考として渡されたアルファ付きMOVは `--transition-asset 'assets/transitions/切り替え.mov'` で映像の上に重ねる。素材内で画面が最も覆われる位置を確認できた場合は `--transition-anchor 0.5` のように秒数を指定する。トランジションに含まれる音声は控えめな音量で重ねる。
