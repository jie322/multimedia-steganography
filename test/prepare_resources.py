"""Derive repeatable test fixtures from the user's JPG and MP4."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "test_resources"
CARRIERS = RESOURCES / "carriers"
PAYLOADS = RESOURCES / "payloads"


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SystemExit("ffmpeg and ffprobe must be on PATH")
    source_image = RESOURCES / "downloads" / "test.jpg"
    videos = sorted((RESOURCES / "downloads").glob("*.mp4"))
    if len(videos) != 1:
        raise SystemExit("Expected exactly one source MP4")
    source_video = videos[0]
    for directory in (CARRIERS, PAYLOADS):
        directory.mkdir(parents=True, exist_ok=True)

    def convert(destination: Path, *options: str) -> None:
        subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(source_video), *options, str(destination)],
            check=True,
        )
        print(f"Prepared {destination.relative_to(ROOT)} ({destination.stat().st_size} bytes)", flush=True)

    with Image.open(source_image) as image:
        image.convert("RGB").save(CARRIERS / "image_carrier.png")
        image.convert("RGB").save(CARRIERS / "image_carrier.bmp")
        small = image.resize((256, 160))
        small.convert("RGBA").save(CARRIERS / "image_rgba.png")
        small.convert("L").save(CARRIERS / "image_grayscale.png")
    shutil.copyfile(source_image, PAYLOADS / "payload_image.jpg")
    convert(CARRIERS / "audio_full.wav", "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2")
    convert(CARRIERS / "audio_20s.wav", "-t", "20", "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2")
    for width, codec in ((1, "pcm_u8"), (2, "pcm_s16le"), (3, "pcm_s24le"), (4, "pcm_s32le")):
        convert(CARRIERS / f"audio_{width * 8}bit.wav", "-t", "0.5", "-vn", "-acodec", codec, "-ar", "16000", "-ac", "1")
    convert(PAYLOADS / "payload_audio.wav", "-t", "3", "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1")
    convert(PAYLOADS / "payload_music.mp3", "-t", "5", "-vn", "-acodec", "libmp3lame", "-b:a", "64k", "-ar", "22050", "-ac", "1")
    convert(PAYLOADS / "payload_video_5s.mp4", "-t", "5", "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "scale=640:-2", "-c:v", "libx264", "-preset", "fast", "-crf", "28",
            "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart")
    (PAYLOADS / "secret_text_utf8.txt").write_text(
        "\u6bd5\u4e1a\u8bbe\u8ba1\u9690\u5199\u6d4b\u8bd5\uff1a\u4e2d\u6587\u3001English\u3001\u65e5\u672c\u8a9e\u3002\n"
        "Line 2: 0123456789 <tag> & symbols.\n", encoding="utf-8")
    (PAYLOADS / "payload_binary.bin").write_bytes(bytes(range(256)) * 16)
    files = [source_image, source_video, *sorted(CARRIERS.iterdir()), *sorted(PAYLOADS.iterdir())]
    manifest = {
        "sourceVideoProbe": json.loads(subprocess.check_output(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source_video)],
            encoding="utf-8")),
        "files": [{"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files if path.is_file()],
    }
    (RESOURCES / "generated_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
