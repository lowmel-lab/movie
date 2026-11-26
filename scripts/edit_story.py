import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from edit_preview import kept_ranges, read_clip, render
from style_tracks import finish


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    style: str
    text: str | list[str]
    sound: str | None = None


def word_bounds(span, words):
    if (not isinstance(span, list) or len(span) != 2
            or any(type(value) is not int for value in span)
            or not 0 <= span[0] <= span[1] < len(words)):
        raise ValueError(f"文字起こしにない語番号の範囲です: {span}")
    return tuple(span)


def snap(start, end, duration, fps):
    last_frame = max(1, round(duration * fps))
    first = min(math.floor(start * fps), last_frame - 1)
    last = min(last_frame, max(first + 1, math.ceil(end * fps)))
    return first / fps, last / fps


def clip_ranges(clip, words, keep, fps, keep_pauses):
    if keep is None:
        return [(None, None, *snap(start, end, clip.duration, fps))
                for start, end in kept_ranges(clip, keep_pauses or bool(words))]
    if not isinstance(keep, list) or not keep:
        raise ValueError("keep には残す語の範囲を指定してください")
    cuts = []
    previous = -1
    for span in keep:
        first, last = word_bounds(span, words)
        if first <= previous:
            raise ValueError(f"keep は話した順で、重複のない範囲にしてください: {span}")
        start = words[first]["start"]
        end = words[last]["end"]
        # Leave room around speech, without pulling an intentionally omitted word back in.
        floor = words[first - 1]["end"] if first else 0.0
        ceiling = words[last + 1]["start"] if last + 1 < len(words) else clip.duration
        start = min(start, max(floor, start - 0.12))
        end = max(end, min(ceiling, end + 0.24))
        start, end = snap(start, end, clip.duration, fps)
        if cuts and start < cuts[-1][3]:
            raise ValueError(f"カット前後の語が近すぎます。自然な位置に修正してください: {span}")
        cuts.append((first, last, start, end))
        previous = last
    return cuts


def shift_cue(event, style, words, cuts, offsets):
    first, last = word_bounds(event.get("words"), words)
    for (left, right, start, end), offset in zip(cuts, offsets):
        if left is None or left <= first <= last <= right:
            t0 = max(start, words[first]["start"])
            t1 = words[last]["end"]
            if style != "Caption":
                t1 = max(t1, t0 + 0.8)
            if t0 < min(t1, end):
                break
    else:
        raise ValueError(f"テロップがカットをまたいでいます: {event['words']}")
    if style == "Diagram":
        text = event.get("labels")
        if (not isinstance(text, list) or not 2 <= len(text) <= 3
                or any(not isinstance(label, str) or not label.strip() or len(label) > 16
                       or "\n" in label for label in text)):
            raise ValueError("図解の labels は各16文字以内のラベルを2〜3個指定してください")
    else:
        text = event.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.splitlines()) > 2:
            raise ValueError("テロップの text は空白を除き1〜2行で指定してください")
    sound = event.get("sound")
    if sound is not None and (style == "Caption" or sound not in ("bright", "down")):
        raise ValueError("効果音は強調か図解で bright / down を指定できます")
    return Cue(offset + t0 - start, offset + min(t1, end) - start, style, text, sound)


def plan_timeline(clips, transcript, plan):
    if len(clips) != len(plan["clips"]) or len(clips) != len(transcript["clips"]):
        raise ValueError("設計図・文字起こし・入力動画の本数を揃えてください")
    frame_rate = clips[0].frame_rate
    cues = []
    ranges = []
    seams = []
    elapsed = 0.0
    for clip, item, settings in zip(clips, transcript["clips"], plan["clips"]):
        words = item["words"]
        previous = 0.0
        for word in words:
            start, end = word["start"], word["end"]
            if not previous <= start < end <= clip.duration + .02:
                raise ValueError("文字起こしの時刻は動画内で開始順に並べてください")
            previous = start
        cuts = clip_ranges(clip, words, settings.get("keep"), frame_rate,
                           bool(settings.get("keep_pauses")))
        offsets = []
        for _, _, start, end in cuts:
            offsets.append(elapsed)
            elapsed += end - start
        ranges.append([(start, end) for _, _, start, end in cuts])
        seams.append(elapsed)
        for key, style in (("captions", "Caption"), ("highlights", "Highlight"),
                           ("diagrams", "Diagram")):
            cues.extend(shift_cue(event, style, words, cuts, offsets)
                        for event in settings.get(key, []))

    cues.sort(key=lambda cue: cue.start)
    end_by_layer = {"Caption": 0.0, "Center": 0.0}
    last_sound = {}
    for cue in cues:
        layer = "Caption" if cue.style == "Caption" else "Center"
        if cue.start < end_by_layer[layer] - .005:
            raise ValueError(f"{cue.start:.2f}秒のテロップが同じ位置で重複しています")
        end_by_layer[layer] = cue.end
        if cue.sound:
            if cue.start - last_sound.get(cue.sound, -math.inf) < 30:
                raise ValueError(f"同じ効果音が30秒以内に重複しています: {cue.start:.2f}秒")
            last_sound[cue.sound] = cue.start
    return ranges, cues, seams[:-1]


def main():
    parser = argparse.ArgumentParser(description="語番号で指定した設計図から動画を作る")
    parser.add_argument("--words", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--assets", type=Path, default=Path("assets"))
    parser.add_argument("--transition", choices=("none", "fade"), default="none")
    parser.add_argument("--transition-asset", type=Path, help="切り替えに重ねるアルファ付き動画")
    parser.add_argument("--transition-anchor", type=float, help="素材内でカットが隠れる秒数")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="書き出さずに設計図だけ確認する")
    args = parser.parse_args()
    try:
        output = args.output.absolute()
        if output.suffix.lower() != ".mp4" or output.exists() or output.is_symlink():
            raise ValueError("新しい .mp4 の出力パスを指定してください")
        transcript = json.loads(args.words.read_text(encoding="utf-8"))
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        clips = [read_clip(Path(item["path"]).resolve(strict=True)) for item in transcript["clips"]]
        if not clips:
            raise ValueError("動画がありません")
        ranges, cues, seams = plan_timeline(clips, transcript, plan)
        transition = read_clip(args.transition_asset.resolve(strict=True)) if args.transition_asset else None
        if args.transition_anchor is not None and (
            transition is None or not 0 <= args.transition_anchor <= transition.duration
        ):
            raise ValueError("切り替え位置はトランジション素材内の秒数を指定してください")
        bgm = plan.get("bgm", "auto")
        font = plan.get("font", "Noto Sans CJK JP")
        if bgm not in ("auto", "off") or not isinstance(font, str) or not font.strip():
            raise ValueError("bgm は auto / off、font はフォントファミリー名を指定してください")
        if any(character in font for character in ",\n\r"):
            raise ValueError("フォントファミリー名にカンマと改行は使えません")
        print(f"設計図OK: {sum(map(len, ranges))}区間、{len(cues)}テロップ・図解", flush=True)
        if args.check:
            return 0
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".movie-story-", dir=output.parent) as directory:
            work = Path(directory)
            base = work / "base.mp4"
            canvas = render(clips, ranges, base, narration_target=-14, frame_aligned=True)
            final = finish(base, canvas, cues, args.assets.resolve(), work, bgm, font,
                           seams, args.transition, transition, args.transition_anchor)
            result = read_clip(final)
            if result.audio_index is None or abs(result.duration - canvas.duration) > .2:
                raise ValueError("書き出した動画の時間または音声ストリームが一致しません")
            os.link(final, output)
        print(f"{output} ({result.width}×{result.height}, {result.duration:.2f}秒)")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"編集エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
