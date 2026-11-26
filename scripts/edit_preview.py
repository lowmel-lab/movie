import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory


SILENCE_EVENT = re.compile(
    r"^\[silencedetect @ [^\]]+\] silence_(start|end): "
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
    re.MULTILINE,
)
PAUSE_THRESHOLD = 1.6
PAUSE_PADDING = 0.30
AUDIO_CLOCK = "aresample=48000:async=1:first_pts=0"


@dataclass(frozen=True)
class Clip:
    path: Path
    duration: float
    video_index: int
    audio_index: int | None
    width: int
    height: int
    frame_rate: Fraction


def run(*args, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, errors="replace", cwd=cwd)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"{args[0]} の実行に失敗しました")
    return result


def probe(path):
    result = run("ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_format",
                 "-show_streams", "-of", "json", str(path))
    return json.loads(result.stdout)


def positive_ratio(value, default):
    try:
        ratio = Fraction(str(value).replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        return default
    return ratio if ratio > 0 else default


def even_size(pixels):
    return max(2, round(pixels / 2) * 2)


def read_clip(path):
    streams = probe(path)
    video = next((s for s in streams["streams"] if s["codec_type"] == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise ValueError(f"映像ストリームがありません: {path}")

    duration = next((float(value) for value in (video.get("duration"),
                     streams.get("format", {}).get("duration"))
                     if value not in (None, "N/A") and math.isfinite(float(value))
                     and float(value) > 0), None)
    if duration is None:
        raise ValueError(f"動画の長さを取得できません: {path}")

    sar = positive_ratio(video.get("sample_aspect_ratio"), Fraction(1))
    width, height = video["width"] * sar, video["height"]
    rotation = next((data["rotation"] for data in video.get("side_data_list", [])
                     if "rotation" in data), video.get("tags", {}).get("rotate", 0))
    if abs(round(float(rotation) / 90)) % 2:
        width, height = height, width
    frame_rate = min(positive_ratio(video.get("avg_frame_rate"), Fraction(30)), 60)
    return Clip(path, duration, video["index"], audio["index"] if audio else None,
                even_size(width), even_size(height), frame_rate)


def silence_ranges(clip):
    result = run("ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info",
                 "-protocol_whitelist", "file", "-i", str(clip.path),
                 "-map", f"0:{clip.audio_index}",
                 "-af", f"{AUDIO_CLOCK},silencedetect=noise=-42dB:d={PAUSE_THRESHOLD}",
                 "-f", "null", "-")
    start = None
    for event, value in SILENCE_EVENT.findall(result.stderr):
        timestamp = max(0.0, min(float(value), clip.duration))
        if event == "start":
            start = timestamp
        elif start is not None:
            yield start, timestamp
            start = None
    if start is not None:
        yield start, clip.duration


def kept_ranges(clip, keep_pauses):
    whole_clip = [(0.0, clip.duration)]
    if keep_pauses or clip.audio_index is None:
        return whole_clip

    ranges = []
    cursor = 0.0
    for start, end in silence_ranges(clip):
        if end - start < PAUSE_THRESHOLD - 0.01:
            continue
        starts_at_edge = start < 0.05
        ends_at_edge = end >= clip.duration - 0.05
        if starts_at_edge and ends_at_edge:
            return whole_clip
        cut_start = 0.0 if starts_at_edge else start + PAUSE_PADDING
        cut_end = clip.duration if ends_at_edge else end - PAUSE_PADDING
        if cut_start > cursor:
            ranges.append((cursor, cut_start))
        cursor = cut_end
    if cursor < clip.duration:
        ranges.append((cursor, clip.duration))
    return ranges or whole_clip


def filter_graph(clips, ranges, *, narration_target=-16, frame_aligned=False):
    canvas = clips[0]
    frame_rate = canvas.frame_rate
    video_format = (
        "scale='max(2,trunc(iw*if(gt(sar,0),sar,1)/2)*2)':ih,setsar=1,"
        f"scale={canvas.width}:{canvas.height}:force_original_aspect_ratio=decrease:"
        f"force_divisible_by=2,pad={canvas.width}:{canvas.height}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,format=yuv420p"
    )
    if not frame_aligned:
        video_format += f",fps={frame_rate}"
    filters = []
    segments = []
    for index, (clip, clip_ranges) in enumerate(zip(clips, ranges)):
        count = len(clip_ranges)
        video_sources = [f"[video_source_{index}_{part}]" for part in range(count)]
        audio_sources = [f"[audio_source_{index}_{part}]" for part in range(count)]
        video_input = f"[{index}:{clip.video_index}]"
        if frame_aligned:
            video_input += f"fps={frame_rate}:start_time=0,"
        filters.append(f"{video_input}split={count}{''.join(video_sources)}")
        if clip.audio_index is None:
            audio = "anullsrc=r=48000:cl=stereo"
        else:
            audio = (f"[{index}:{clip.audio_index}]{AUDIO_CLOCK},"
                     "aformat=channel_layouts=stereo,apad")
        filters.append(f"{audio},asplit={count}{''.join(audio_sources)}")

        for (start, end), video, audio in zip(clip_ranges, video_sources, audio_sources):
            number = len(segments)
            window = f"start={start:.6f}:end={end:.6f}"
            video_window = window
            if frame_aligned:
                video_window = (f"start_frame={round(start * frame_rate)}:"
                                f"end_frame={round(end * frame_rate)}")
            filters.append(f"{video}trim={video_window},"
                           f"setpts=PTS-STARTPTS,{video_format}[v{number}]")
            filters.append(f"{audio}atrim={window},asetpts=PTS-STARTPTS[a{number}]")
            segments.append(f"[v{number}][a{number}]")
    filters.append(f"{''.join(segments)}concat=n={len(segments)}:v=1:a=1[video][joined]")
    filters.append(f"[joined]loudnorm=I={narration_target}:TP=-1.5:LRA=11[audio]")
    return ";\n".join(filters)


def render(clips, ranges, output, *, narration_target=-16, frame_aligned=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".movie-", dir=output.parent) as temp:
        graph_file = Path(temp) / "filters.txt"
        graph = filter_graph(clips, ranges, narration_target=narration_target,
                             frame_aligned=frame_aligned)
        graph_file.write_text(graph, encoding="utf-8")
        draft = Path(temp) / "draft.mp4"
        inputs = []
        for clip in clips:
            inputs.extend(("-protocol_whitelist", "file", "-i", str(clip.path)))
        run("ffmpeg", "-nostdin", "-hide_banner", "-v", "error", *inputs,
            "-filter_complex_script", str(graph_file), "-map", "[video]", "-map", "[audio]",
            "-map_metadata", "-1", "-map_chapters", "-1", "-c:v", "libx264",
            "-preset", "fast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", str(draft))

        result = read_clip(draft)
        if result.audio_index is None:
            raise ValueError("書き出しに音声ストリームがありません")
        os.link(draft, output)  # An existing file, including a competing render, wins.
    return result


def main():
    parser = argparse.ArgumentParser(description="確認用のMP4を作成します。")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--keep-pauses", action="store_true", help="無音部分を残す")
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()

    try:
        for command in ("ffmpeg", "ffprobe"):
            if shutil.which(command) is None:
                raise ValueError(f"{command} が見つかりません。FFmpegを用意してください。")
        output = args.output.absolute()
        if output.suffix.lower() != ".mp4":
            raise ValueError("出力名には .mp4 を指定してください")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"すでに出力先にファイルがあります: {output}")

        clips = [read_clip(path.resolve(strict=True)) for path in args.inputs]
        ranges = []
        for clip in clips:
            retained = kept_ranges(clip, args.keep_pauses)
            ranges.append(retained)
            kept_seconds = sum(end - start for start, end in retained)
            print(f"{clip.path.name}: {clip.duration:.2f}秒 → {kept_seconds:.2f}秒", flush=True)

        result = render(clips, ranges, output)
    except (OSError, ValueError) as error:
        print(f"編集エラー: {error}", file=sys.stderr)
        return 1

    print(f"{output} ({result.width}×{result.height}, {result.duration:.2f}秒)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
