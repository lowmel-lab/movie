# Google Driveの転送と保存

通常はGoogle Driveコネクタの **LOWMEL** 接続を使う。完成先は、そのアカウントのマイドライブ直下。保存先の指定があれば、同じ接続でフォルダを確認する。認証情報はリポジトリに保存しない。

## 素材を取り込む

1. 素材のメタデータを読み、ID、種類、サイズ、アクセスを確認する。フォルダはページ付き一覧を最後まで読み、指定範囲のサブフォルダも調べる。対象となる動画サイズの合計と空き容量を確認し、中間ファイルとMP4の領域を見込む。
2. 動画は `fetch(download_raw_file=true, include_base64=false)` でファイル参照として取得する。返された `workspace_path` または利用可能なファイル参照の展開機能で作業領域に置く。32 MiB以下の `sediment://` 参照は `download_file` で展開できる。ローカルのサイズと `ffprobe` を確認し、ファイル名の自然順で編集に渡す。
3. コネクタの実測上限はファイル単位で256 MiB。上限を超える場合や作業環境へ展開できない場合は下記の大容量用経路を使う。未認証なら扱える素材と確認できた制限を伝え、LOWMELでの認証または作業環境から読める素材が必要なことを伝える。

## 完成品を保存する

確認済みMP4だけを保存する。コネクタのアップロードに絶対パスと `video/mp4` を渡す。マイドライブ直下には `parent_folder_id` を渡さない。案件名や日時を含む新しい名前を使い、返されたIDのメタデータで名前・サイズ・保存先とリンクを確認する。直前のアップロードがタイムアウトしたら、同名のファイルができていないか確認してから再試行する。

## 大容量の転送

GB級の動画やフォルダは、LOWMELで一度OAuth認証済みの `lowmel` というGoogle Drive用rclone remoteがある環境で転送できる。アカウントを選ぶ認証は利用者が行い、認証情報はリポジトリ外に置く。`rclone listremotes` で認証済み設定を確認する。元のフォルダの公開範囲を変更しない。

```sh
python3 scripts/drive_transfer.py download '<DriveのフォルダURL>' --directory output/案件/sources
# 単一ファイルには、Driveで確認した名前と分かる場合はサイズを渡す
python3 scripts/drive_transfer.py download '<Driveの動画URL>' --name '素材.mov' --size 4294967296 --directory output/案件/sources
python3 scripts/drive_transfer.py upload output/案件/初稿.mp4 --name '案件名-初稿.mp4'
```

アップロードは分割転送、ダウンロードはファイル単位のストリーミングと再試行を使う。戻り値の `id` はLOWMELのコネクタで読み返し、そこで得たリンクを返す。実ファイルではDriveの権限・空き容量・転送制限と、作業環境の空き容量を確認する。
