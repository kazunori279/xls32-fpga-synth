"""Assemble the captioned report .mp4: for each test, a Pillow caption card (silent,
~3 s) followed by that test's scrolling-spectrogram clip; all clips are encoded to
identical params and joined with the ffmpeg concat demuxer into one video."""
import os, subprocess

VW, VH, FPS = 1000, 400, 25
_VCHAIN = f"scale={VW}:{VH}:force_original_aspect_ratio=disable,setsar=1,fps={FPS},format=yuv420p"

def _run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + " ".join(cmd) + "\n" + r.stderr.decode()[-1500:])

def card_clip(png, out, secs=3.0):
    _run(["ffmpeg", "-y", "-loop", "1", "-t", f"{secs}", "-i", png,
          "-f", "lavfi", "-t", f"{secs}", "-i", "anullsrc=r=44100:cl=mono",
          "-vf", _VCHAIN, "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
          "-pix_fmt", "yuv420p", "-shortest", out])

def spectro_clip(wav, out):
    # scrolling spectrogram on an 8 kHz copy (log freq axis) + the audio at 44.1 kHz.
    flt = (f"[0:a]asplit=2[s][m];"
           f"[s]aresample=8000,showspectrum=s={VW}x{VH}:mode=combined:slide=scroll:"
           f"color=intensity:scale=cbrt:fscale=log,{_VCHAIN}[v];"
           f"[m]aresample=44100,aformat=channel_layouts=mono[a]")
    _run(["ffmpeg", "-y", "-i", wav, "-filter_complex", flt, "-map", "[v]", "-map", "[a]",
          "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p", out])

def concat(clips, out):
    lst = out + ".txt"
    with open(lst, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", out])
    os.remove(lst)

def build(segments_dir, entries, out_mp4):
    """entries: ordered list of dicts {kind:'card'|'test', png, wav?, id}. Returns out_mp4."""
    os.makedirs(segments_dir, exist_ok=True)
    clips = []
    for i, e in enumerate(entries):
        if e["kind"] == "card":
            c = os.path.join(segments_dir, f"{i:03d}_{e['id']}_card.mp4")
            card_clip(e["png"], c, e.get("secs", 3.0)); clips.append(c)
        else:  # test = caption card + spectrogram
            c1 = os.path.join(segments_dir, f"{i:03d}_{e['id']}_card.mp4")
            c2 = os.path.join(segments_dir, f"{i:03d}_{e['id']}_spec.mp4")
            card_clip(e["png"], c1, e.get("secs", 3.0)); clips.append(c1)
            spectro_clip(e["wav"], c2); clips.append(c2)
    concat(clips, out_mp4)
    return out_mp4
