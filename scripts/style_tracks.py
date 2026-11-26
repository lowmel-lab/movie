import math
import sys
import wave
from array import array

from edit_preview import run


SAMPLE_RATE = 24000
# A quiet original four-bar pad. These tones and effects are synthesized locally.
CHORDS = ((48, 55, 59, 64), (45, 52, 55, 60), (41, 48, 52, 57), (43, 50, 57, 62))
AUDIO_TYPES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff"}


def write_wave(path, samples):
    data = array("h", (round(32767 * sample) for sample in samples))
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as track:
        track.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        track.writeframes(data.tobytes())
    return path


def music_samples():
    beat = 60 / 86
    for chord in CHORDS:
        frequencies = [440 * 2 ** ((note - 69) / 12) for note in chord]
        for i in range(round(beat * 4 * SAMPLE_RATE)):
            t = i / SAMPLE_RATE
            envelope = max(0, min(1, t / .20, (beat * 4 - t) / .38))
            pulse = .76 + .24 * math.cos(math.tau * t / beat)
            signal = sum(math.sin(math.tau * hz * t) + .09 * math.sin(2 * math.tau * hz * t)
                         for hz in frequencies)
            yield .12 * envelope * pulse * signal


def effect_samples(kind):
    first, last = (610, 1150) if kind == "bright" else (480, 155)
    duration = .37
    for i in range(round(SAMPLE_RATE * duration)):
        t = i / SAMPLE_RATE
        phase = math.tau * (first * t + (last - first) * t * t / (2 * duration))
        envelope = min(1, t / .010) * (1 - t / duration) ** 2
        yield .55 * envelope * (math.sin(phase) + .15 * math.sin(2 * phase))


def audio_asset(directory, preferred, *, any_name=False):
    if not directory.is_dir():
        return None
    files = sorted(path for path in directory.iterdir()
                   if path.is_file() and path.suffix.lower() in AUDIO_TYPES)
    return next((path for path in files if path.stem.lower() == preferred),
                files[0] if files and any_name else None)


def ass_time(seconds):
    minutes, rest = divmod(round(seconds * 100), 6000)
    hours, minutes = divmod(minutes, 60)
    seconds, centiseconds = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def ass_text(text):
    return (text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N"))


def write_ass(path, cues, canvas, font):
    portrait = canvas.height > canvas.width
    h = 1920 if portrait else 1080
    w = round(h * canvas.width / canvas.height)
    text_size = round(min(w * .061, h * .060))
    x_margin = round(w * .055)
    y_margin = round(h * (.16 if portrait else .07))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{text_size},&H00FFFFFF,&H00FFFFFF,&H00111111,&H60000000,-1,0,0,0,100,100,0,0,1,4,2,2,{x_margin},{x_margin},{y_margin},1
Style: Highlight,{font},{round(text_size * 1.28)},&H0058F2FF,&H00FFFFFF,&H00181713,&H60000000,-1,0,0,0,100,100,0,0,1,6,2,5,{x_margin},{x_margin},0,1
Style: Diagram,{font},{round(text_size * .78)},&H00FFFFFF,&H00FFFFFF,&H20293820,&H20293820,-1,0,0,0,100,100,0,0,3,16,0,5,{x_margin},{x_margin},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        start, end = ass_time(cue.start), ass_time(cue.end)
        if start == end:
            continue
        if cue.style == "Diagram":
            items = r"\N↓\N".join(ass_text(label) for label in cue.text)
        else:
            items = ass_text(cue.text)
        layer = 0 if cue.style == "Caption" else 1
        events.append(f"Dialogue: {layer},{start},{end},{cue.style},,0,0,0,,{items}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def add_transitions(inputs, filters, mixes, canvas, seams, mode, asset, anchor, first_index):
    current = "[0:v:0]"
    if not seams or (asset is None and mode == "none"):
        return current
    width, height = canvas.width, canvas.height
    duration = asset.duration if asset else .40
    anchor = anchor if anchor is not None else duration / 2
    last_end = 0.0
    index = first_index
    for number, seam in enumerate(seams):
        start = max(0, seam - anchor)
        if start < last_end or start >= canvas.duration:
            continue
        last_end = start + duration
        overlay = f"[transition{number}]"
        if asset:
            inputs.extend(("-protocol_whitelist", "file", "-i", str(asset.path)))
            filters.append(f"[{index}:{asset.video_index}]scale={width}:{height}:"
                           f"force_original_aspect_ratio=increase,crop={width}:{height},"
                           f"setsar=1,format=rgba,setpts=PTS-STARTPTS+{start:.6f}/TB{overlay}")
            if asset.audio_index is not None:
                effect = f"[transition_sound{number}]"
                filters.append(f"[{index}:{asset.audio_index}]aresample=48000,"
                               f"aformat=channel_layouts=stereo,volume=-13dB,"
                               f"adelay={round(start * 1000)}:all=1{effect}")
                mixes.append(effect)
            index += 1
        else:
            filters.append(f"color=white:s={width}x{height}:r={canvas.frame_rate}:d={duration},"
                           "format=rgba,fade=t=in:st=0:d=0.20:alpha=1,"
                           f"fade=t=out:st=0.20:d=0.20:alpha=1,setpts=PTS+{start:.6f}/TB{overlay}")
        composed = f"[transitioned{number}]"
        filters.append(f"{current}{overlay}overlay=eof_action=pass:repeatlast=0:"
                       f"enable='between(t,{start:.6f},{last_end:.6f})'{composed}")
        current = composed
    return current


def finish(base, canvas, cues, assets, work, bgm, font, seams, transition_mode, transition, anchor):
    length = canvas.duration
    inputs = ["-i", str(base)]
    chains = []
    mixes = ["[0:a:0]"]
    index = 1
    if bgm == "auto":
        music = audio_asset(assets / "bgm", "default", any_name=True)
        if music is None:
            music = write_wave(work / "music.wav", music_samples())
        inputs.extend(("-stream_loop", "-1", "-protocol_whitelist", "file", "-i", str(music)))
        fade = min(.45, length / 4)
        chains.append(f"[{index}:a:0]atrim=duration={length:.6f},"
                      "loudnorm=I=-32:TP=-9:LRA=7,aresample=48000,"
                      f"afade=t=in:d={fade:.6f},afade=t=out:st={length - fade:.6f}:d={fade:.6f},"
                      "aformat=channel_layouts=stereo[bgm]")
        mixes.append("[bgm]")
        index += 1

    effects = {}
    for cue in cues:
        if not cue.sound:
            continue
        if cue.sound not in effects:
            effect = audio_asset(assets / "sfx", cue.sound)
            effects[cue.sound] = effect or write_wave(work / f"{cue.sound}.wav", effect_samples(cue.sound))
        inputs.extend(("-protocol_whitelist", "file", "-i", str(effects[cue.sound])))
        gain = -15 if cue.sound == "bright" else -17
        delay = round(cue.start * 1000)
        chains.append(f"[{index}:a:0]atrim=duration=2,asetpts=PTS-STARTPTS,aresample=48000,"
                      f"aformat=channel_layouts=stereo,volume={gain}dB,adelay={delay}:all=1[sfx{index}]")
        mixes.append(f"[sfx{index}]")
        index += 1

    video_out = add_transitions(inputs, chains, mixes, canvas, seams, transition_mode, transition, anchor, index)
    chains.append(f"{''.join(mixes)}amix=inputs={len(mixes)}:duration=first:normalize=0,"
                  "alimiter=limit=0.86:level=0:latency=1[audio]")
    if cues:
        write_ass(work / "cues.ass", cues, canvas, font)
        filter_spec = "ass=cues.ass"
        if (assets / "fonts").is_dir():
            (work / "fonts").symlink_to(assets / "fonts", target_is_directory=True)
            filter_spec += ":fontsdir=fonts"
        chains.append(f"{video_out}{filter_spec}[subtitled]")
        video_out = "[subtitled]"
    if video_out == "[0:v:0]":
        video_map = "0:v:0"
        video = ["-c:v", "copy"]
    else:
        video_map = video_out
        video = ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
    final = work / "final.mp4"
    run("ffmpeg", "-nostdin", "-v", "error", *inputs, "-filter_complex", ";".join(chains),
        "-map", video_map, "-map", "[audio]", *video, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{length:.6f}", "-movflags", "+faststart", str(final), cwd=work)
    return final
