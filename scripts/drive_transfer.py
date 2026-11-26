import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DRIVE_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
REMOTE = re.compile(r"[A-Za-z0-9_-]+\Z")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mts", ".m2ts", ".wmv", ".mpg"}
TRANSFER_FLAGS = ("--buffer-size", "16M", "--transfers", "1", "--checkers", "2",
                  "--retries", "3", "--low-level-retries", "10", "--stats", "30s", "-v")


def drive_location(url):
    address = urlsplit(url)
    if address.scheme != "https" or address.netloc != "drive.google.com":
        raise ValueError("https://drive.google.com のファイルまたはフォルダリンクを指定してください")
    query = parse_qs(address.query)
    parts = address.path.strip("/").split("/")
    folder = "folders" in parts[:-1]
    if folder:
        file_id = parts[parts.index("folders") + 1]
    elif "d" in parts[:-1]:
        file_id = parts[parts.index("d") + 1]
    else:
        file_id = query.get("id", [""])[0]
    key = query.get("resourcekey", [""])[0]
    if not DRIVE_ID.fullmatch(file_id) or (key and not DRIVE_ID.fullmatch(key)):
        raise ValueError("Google Driveのリンクから有効なIDを取得できません")
    return file_id, folder, key


def rclone_json(*args):
    result = subprocess.run(("rclone", *args), check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def transfer(*args):
    subprocess.run(("rclone", *args, *TRANSFER_FLAGS), check=True, stdout=sys.stderr)


def natural_key(value):
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"([0-9]+)", value)]


def space_check(directory, total):
    free = shutil.disk_usage(directory).free
    # Staging, an intermediate edit and the final encode coexist during rendering.
    minimum = total * 2 + 512 * 1024**2
    if free < minimum:
        raise ValueError(f"作業領域が不足しています: 空き {free / 1024**3:.1f} GiB、"
                         f"目安 {minimum / 1024**3:.1f} GiB")


def download(args):
    media_id, folder, key = drive_location(args.url)
    key_flags = ("--drive-resource-key", key) if key else ()
    if folder or args.folder:
        entries = rclone_json("lsjson", args.remote, "--drive-root-folder-id", media_id,
                              "--recursive", "--files-only", "--drive-skip-gdocs",
                              "--drive-skip-shortcuts", *key_flags)
        entries = [item for item in entries if Path(item["Name"]).suffix.lower() in VIDEO_EXTENSIONS
                   or item.get("MimeType", "").startswith("video/")]
        entries.sort(key=lambda item: (natural_key(item["Path"]), item["ID"]))
    else:
        if not args.name:
            raise ValueError("ファイルリンクにはDriveで確認した --name を指定してください")
        entries = [{"ID": media_id, "Name": args.name, "Size": args.size}]
    if not entries:
        raise ValueError("対象フォルダに動画がありません")

    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    space_check(directory, sum(max(0, item.get("Size") or 0) for item in entries))
    results = []
    for index, item in enumerate(entries, 1):
        if not DRIVE_ID.fullmatch(item["ID"]):
            raise ValueError("Driveから有効なファイルIDを取得できません")
        name = Path(item["Name"]).name
        if not name or name in (".", ".."):
            raise ValueError("Driveから有効なファイル名を取得できません")
        path = directory / f"{index:04}-{item['ID']}-{name}"
        if path.is_symlink():
            raise ValueError(f"取り込み先にシンボリックリンクがあります: {path.name}")
        transfer("backend", "copyid", args.remote, item["ID"], str(path), "--checksum", *key_flags)
        size = path.stat().st_size
        expected = item.get("Size")
        if size <= 0 or (expected is not None and expected >= 0 and size != expected):
            raise ValueError(f"Driveのファイルサイズと一致しません: {name}")
        results.append({"id": item["ID"], "path": str(path), "size": size})
    return results


def upload(args):
    file = args.file.resolve(strict=True)
    name = args.name or file.name
    if Path(name).name != name or name in (".", "..") or not file.is_file():
        raise ValueError("有効なファイルと出力ファイル名を指定してください")
    root = args.folder_id or "root"
    if not DRIVE_ID.fullmatch(root):
        raise ValueError("Google DriveのフォルダIDが無効です")
    target = args.remote + name
    flags = ("--drive-root-folder-id", root)
    transfer("copyto", str(file), target, "--immutable", "--checksum", "--drive-chunk-size", "16M", *flags)
    result = rclone_json("lsjson", "--stat", target, *flags)
    if not result.get("ID") or result["Size"] != file.stat().st_size:
        raise ValueError("Driveへの保存後にファイルIDまたはサイズを確認できません")
    return {"id": result["ID"], "name": result["Name"], "size": result["Size"]}


def main():
    parser = argparse.ArgumentParser(description="認証済みの rclone を使ってDriveの動画を転送する")
    parser.add_argument("--remote", default="lowmel", help="LOWMELで認証済みの rclone remote の名前")
    commands = parser.add_subparsers(dest="action", required=True)
    incoming = commands.add_parser("download")
    incoming.add_argument("url")
    incoming.add_argument("--directory", type=Path, required=True)
    incoming.add_argument("--name", help="単一ファイルのDrive上の名前")
    incoming.add_argument("--size", type=int, help="単一ファイルのサイズ（バイト）")
    incoming.add_argument("--folder", action="store_true", help="共有リンクをフォルダとして扱う")
    outgoing = commands.add_parser("upload")
    outgoing.add_argument("file", type=Path)
    outgoing.add_argument("--name")
    outgoing.add_argument("--folder-id", help="省略すると認証アカウントのマイドライブ直下")
    args = parser.parse_args()
    try:
        if not REMOTE.fullmatch(args.remote):
            raise ValueError("rclone の設定済み remote 名を指定してください")
        args.remote += ":"
        result = download(args) if args.action == "download" else upload(args)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None) or str(error)
        print(f"Drive転送エラー: {detail.strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
