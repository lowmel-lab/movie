# movie

Codexでこのリポジトリを開き、**Google Driveの動画・フォルダのリンクを送る**か、動画を添付して **「編集をお願い」** と送ると、`movie-edit` スキルが編集を始めます。素材は `input/` に置いても使えます。

文字起こしに沿って不要な間や言い直しを整理し、話の区切りでテロップを切り替えます。見せ場には強調・図解や効果音を使い、必要に応じてBGMを入れます。画角は先頭素材に合わせ、編集や保存先の指定があれば優先します。

完成先は **LOWMELのGoogle Driveのマイドライブ** です。GB級の専用転送にはLOWMELで認証済みのrclone設定が必要です。[Driveの手順](.agents/skills/movie-edit/references/drive.md)

## 音源とフォントの差し替え

- `assets/bgm/default.mp3` にお持ちのBGMを置けます。
- `assets/sfx/bright.wav` と `assets/sfx/down.wav` に明るい効果音と落ち着いた効果音を置けます。
- `assets/fonts/` にフォントを置けます。編集時にフォントファミリー名を指定してください。

音源がない場合は、このリポジトリで生成する音源を使います。素材・音源・文字起こし・書き出しはGitの管理対象外です。

## コマンドで実行する場合

Python 3.10以降とFFmpeg（`ffmpeg`、`ffprobe`、H.264・AAC、libass）が必要です。文字起こしの初期設定とテロップつきの書き出しは [設計と実行](.agents/skills/movie-edit/references/edit-plan.md) を参照してください。

無音カット・音量調整・結合だけの簡易編集も実行できます。

```sh
python3 scripts/edit_preview.py --output output/初稿.mp4 -- input/01.mov input/02.mp4
```

曲や間を残したい場合は `--keep-pauses` を使います。
