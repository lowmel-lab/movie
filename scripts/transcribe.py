import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import unicodedata
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from edit_preview import read_clip, run


MODEL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL}.tar.bz2"
MODEL_SHA256 = "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e"
MODEL_FILES = ("model.int8.onnx", "tokens.txt")
RATE = 16000


def prepare_model(cache):
    result = cache / MODEL
    if all((result / name).is_file() for name in MODEL_FILES):
        return result
    cache.mkdir(parents=True, exist_ok=True)
    print("初回のみ、文字起こしモデル（約163 MB）を取得します", file=sys.stderr, flush=True)
    with TemporaryDirectory(prefix=".model-", dir=cache) as temp:
        archive = Path(temp) / "model.tar.bz2"
        checksum = hashlib.sha256()
        with urlopen(MODEL_URL, timeout=60) as response, archive.open("wb") as file:
            while chunk := response.read(1024 * 1024):
                checksum.update(chunk)
                file.write(chunk)
        if checksum.hexdigest() != MODEL_SHA256:
            raise ValueError("文字起こしモデルのチェックサムが一致しません")
        result.mkdir(exist_ok=True)
        with tarfile.open(archive, "r:bz2") as release:
            for name in MODEL_FILES:
                member = release.getmember(f"{MODEL}/{name}")
                if not member.isfile():
                    raise ValueError("文字起こしモデルのアーカイブ形式が変わっています")
                staged = Path(temp) / name
                with release.extractfile(member) as source, staged.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.replace(staged, result / name)
    return result


def chunks(audio, np):
    while audio.tell() < audio.getnframes():
        start = audio.tell()
        pcm = audio.readframes(26 * RATE)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if len(samples) == 26 * RATE and audio.tell() < audio.getnframes():
            # Prefer a quiet split in the last four seconds of a recognition window.
            tail = samples[22 * RATE:].reshape(-1, RATE // 10)
            split = 22 * RATE + (int(np.square(tail).mean(axis=1).argmin()) + 1) * (RATE // 10)
            samples = samples[:split]
            audio.setpos(start + split)
        yield start / RATE, samples


def timed_parts(result, offset, length, duration):
    parts = []
    for token, timestamp in zip(result.tokens, result.timestamps):
        text = token.replace("▁", " ")
        start = max(0., min(float(timestamp), length))
        if not text.strip() or start >= length or offset + start >= duration:
            continue
        if parts and (start <= parts[-1][0] or all(unicodedata.category(char)[0] == "P"
                                                 for char in text.strip())):
            parts[-1][1] += text
        else:
            parts.append([start, text])
    for index, (start, text) in enumerate(parts):
        following = parts[index + 1][0] if index + 1 < len(parts) else length
        end = min(following, start + .48, length, duration - offset)
        if end > start:
            yield {"start": round(offset + start, 6), "end": round(offset + end, 6), "text": text}


def recognize(clip, recognizer, np, directory):
    if clip.audio_index is None:
        return []
    audio = directory / "speech.wav"
    run("ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file", "-i", str(clip.path),
        "-map", f"0:{clip.audio_index}", "-af", "aresample=16000:async=1:first_pts=0", "-ac", "1",
        "-t", str(clip.duration), "-c:a", "pcm_s16le", str(audio))
    words = []
    with wave.open(str(audio)) as source:
        for offset, samples in chunks(source, np):
            if len(samples) < RATE // 4 or np.max(np.abs(samples)) < .004:
                continue
            stream = recognizer.create_stream()
            stream.accept_waveform(RATE, samples)
            recognizer.decode_stream(stream)
            words.extend(timed_parts(stream.result, offset, len(samples) / RATE, clip.duration))
    return words


def main():
    parser = argparse.ArgumentParser(description="動画から時刻付き文字起こしを作成する")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".movie-cache/models"))
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--language", choices=("auto", "ja", "en", "zh", "ko", "yue"), default="auto")
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        import numpy as np
        import sherpa_onnx

        output = args.output.absolute()
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"すでに出力先にファイルがあります: {output}")
        clips = [read_clip(path.resolve(strict=True)) for path in args.inputs]
        output.parent.mkdir(parents=True, exist_ok=True)
        transcript = {"clips": []}
        with TemporaryDirectory(prefix=".speech-", dir=output.parent) as directory:
            work = Path(directory)
            recognizer = None
            if any(clip.audio_index is not None for clip in clips):
                model = args.model_dir or prepare_model(args.cache)
                if not all((model / name).is_file() for name in MODEL_FILES):
                    raise ValueError("モデルフォルダに model.int8.onnx と tokens.txt が必要です")
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model / MODEL_FILES[0]), tokens=str(model / MODEL_FILES[1]),
                    language="" if args.language == "auto" else args.language,
                    num_threads=min(4, os.cpu_count() or 1), use_itn=True,
                )
            for clip in clips:
                words = recognize(clip, recognizer, np, work)
                for number, word in enumerate(words):
                    word["id"] = number
                transcript["clips"].append({"path": str(clip.path), "duration": clip.duration,
                                            "words": words})
                print(f"{clip.path.name}: {len(words)}区分", flush=True)
            staged = work / "words.json"
            staged.write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.link(staged, output)
        print(output)
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        print(f"文字起こしエラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
