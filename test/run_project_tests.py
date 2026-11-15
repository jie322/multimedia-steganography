"""Exercise real-media HTTP round trips, capacity limits, and PCM fidelity."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import sys
import threading
import time
import traceback
import wave
from datetime import datetime
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import webui
from stego_lsb.LSBSteg import hide_message_in_image
from stego_lsb.bit_manipulation import lsb_interleave_bytes

RESOURCES = ROOT / "test_resources"
OUTPUT = ROOT / "output" / "testing"
TEXT = (RESOURCES / "payloads" / "secret_text_utf8.txt").read_text(encoding="utf-8")
JPG = RESOURCES / "downloads" / "test.jpg"
WAV = RESOURCES / "carriers" / "audio_20s.wav"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def multipart(fields, files):
    boundary = b"----StegoRealMediaTestBoundary20261027"
    parts = []
    for name, value in fields.items():
        parts.append(b"--" + boundary + b'\r\nContent-Disposition: form-data; name="' + name.encode()
                     + b'"\r\n\r\n' + str(value).encode("utf-8") + b"\r\n")
    for name, (filename, content) in files.items():
        parts.append(b"--" + boundary + b'\r\nContent-Disposition: form-data; name="' + name.encode()
                     + b'"; filename="' + filename.encode("utf-8")
                     + b'"\r\nContent-Type: application/octet-stream\r\n\r\n' + content + b"\r\n")
    return b"".join(parts) + b"--" + boundary + b"--\r\n", boundary.decode()


class Suite:
    def __init__(self):
        class QuietHandler(webui.StegoWebHandler):
            def log_message(self, fmt, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.results = []
        self.metrics = []

    def case(self, name, action):
        started = time.perf_counter()
        result = {"name": name}
        try:
            details = action()
            result.update(status="PASS", details=details)
        except Exception as exc:
            result.update(status="FAIL", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        result["seconds"] = round(time.perf_counter() - started, 3)
        self.results.append(result)
        print(f"{result['status']}: {name} ({result['seconds']}s)" + (f" -- {result['error']}" if result['status'] == 'FAIL' else ''), flush=True)

    def post(self, endpoint, fields=None, files=None, expected=200):
        body, boundary = multipart(fields or {}, files or {})
        request = Request(self.base + endpoint, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urlopen(request, timeout=120) as response:
                status, raw = response.status, response.read()
        except HTTPError as exc:
            status, raw = exc.code, exc.read()
        payload = json.loads(raw)
        require(status == expected, f"Expected HTTP {expected}, received {status}: {payload.get('error', '')}")
        require(payload.get("ok") is (expected == 200), f"Incorrect ok flag: {payload}")
        return payload

    def roundtrip(self, carrier, secret, filename, bits=2, is_text=False, save=None):
        audio = carrier.suffix == ".wav"
        kind, carrier_key, secret_key, stego_key = ("wav", "wav_carrier", "wav_secret_file", "wav_stego") if audio else ("image", "carrier", "secret_file", "stego")
        fields = {"num_lsb": bits, "compression": 6, "payload_mode": "text" if is_text else "file"}
        files = {carrier_key: (carrier.name, carrier.read_bytes())}
        if is_text:
            fields["wav_secret_text" if audio else "secret_text"] = secret.decode("utf-8")
        else:
            files[secret_key] = (filename, secret)
        encoded = self.post(f"/api/{kind}/embed", fields, files)
        content = base64.b64decode(encoded["audio" if audio else "image"].split(",", 1)[1])
        if save:
            (OUTPUT / save).write_bytes(content)
        recovered = self.post(f"/api/{kind}/extract", {"num_lsb": bits}, {stego_key: (encoded["filename"], content)})
        restored = base64.b64decode(recovered["dataBase64"])
        require(restored == secret, "Recovered bytes differ from original")
        require(recovered["stats"]["isUtf8Text"] == is_text, "Text/file mode metadata lost")
        expected_name = webui.build_payload_metadata("secret.txt" if is_text else filename, is_text).filename
        require(recovered["downloadName"] == expected_name, "Original filename metadata lost")
        require(not recovered["stats"]["legacyPayload"], "New payload unexpectedly treated as legacy")
        if is_text:
            require(recovered["text"] == secret.decode("utf-8"), "Unicode text differs")
        if audio:
            with wave.open(str(carrier), "rb") as original, wave.open(BytesIO(content), "rb") as output:
                require(original.getparams() == output.getparams(), "WAV format changed")
        else:
            with Image.open(BytesIO(content)) as output:
                require(output.format == "PNG", "Image output must be lossless PNG")
                original = np.asarray(webui.open_image(carrier.read_bytes()), dtype=np.int16)
                modified = np.asarray(output, dtype=np.int16)
                require(original.shape == modified.shape, "Image dimensions/channels changed")
                delta = np.abs(original - modified)
                require(int(delta.max()) <= (1 << bits) - 1, "Pixel differences exceed chosen LSB bits")
                mse = float(np.mean((original.astype(float) - modified) ** 2))
                psnr = 10 * math.log10(255**2 / mse) if mse else None
                self.metrics.append({"carrier": carrier.name, "payload": filename, "lsb": bits, "psnrDb": psnr, "maxPixelDelta": int(delta.max())})
        if save:
            (OUTPUT / ("recovered_" + filename)).write_bytes(restored)
        return {"sha256": hashlib.sha256(secret).hexdigest(), "payloadBytes": len(secret), "outputBytes": len(content), "lsb": bits}

    def run(self):
        for path in ("/health", "/", "/app.js", "/styles.css"):
            def get(path=path):
                with urlopen(self.base + path) as response:
                    require(response.status == 200 and response.read(), "GET response empty")
            self.case(f"HTTP GET {path}", get)

        for bits in range(1, 9):
            self.case(f"JPG Unicode text roundtrip LSB={bits}", lambda bits=bits: self.roundtrip(JPG, TEXT.encode(), "secret.txt", bits, True, "image_text_lsb2.png" if bits == 2 else None))
            self.case(f"WAV Unicode text roundtrip LSB={bits}", lambda bits=bits: self.roundtrip(WAV, TEXT.encode(), "secret.txt", bits, True, "audio_text_lsb2.wav" if bits == 2 else None))

        for carrier_name in ("image_carrier.png", "image_carrier.bmp", "image_rgba.png", "image_grayscale.png"):
            self.case(f"Image format {carrier_name}", lambda name=carrier_name: self.roundtrip(RESOURCES / "carriers" / name, TEXT.encode(), "secret.txt", is_text=True))
        for filename in ("secret_text_utf8.txt", "payload_binary.bin", "payload_image.jpg", "payload_audio.wav", "payload_music.mp3", "payload_video_5s.mp4"):
            secret = (RESOURCES / "payloads" / filename).read_bytes()
            for carrier in (JPG, WAV):
                self.case(f"{carrier.suffix} file roundtrip {filename}", lambda carrier=carrier, secret=secret, filename=filename: self.roundtrip(carrier, secret, filename, save=f"{carrier.suffix[1:]}_with_{Path(filename).stem}{'.wav' if carrier.suffix == '.wav' else '.png'}"))
        self.case("Unicode payload filename", lambda: self.roundtrip(JPG, b"filename metadata", "\u6bd5\u4e1a\u8bbe\u8ba1\u8f7d\u8377.bin"))
        self.case("Full extracted music carrier", lambda: self.roundtrip(RESOURCES / "carriers" / "audio_full.wav", TEXT.encode(), "secret.txt", is_text=True))

        for width in (1, 2, 3, 4):
            carrier = RESOURCES / "carriers" / f"audio_{width * 8}bit.wav"
            self.case(f"PCM {width * 8}-bit byte recovery", lambda carrier=carrier: self.roundtrip(carrier, TEXT.encode(), "secret.txt", is_text=True))
            def fidelity(carrier=carrier, width=width):
                encoded, _ = webui.embed_wav_secret(carrier.read_bytes(), TEXT.encode(), 2, "secret.txt", True)
                with wave.open(str(carrier), "rb") as sound:
                    original = sound.readframes(sound.getnframes())
                with wave.open(BytesIO(encoded), "rb") as sound:
                    modified = sound.readframes(sound.getnframes())
                def samples(data):
                    return [int.from_bytes(data[i:i+width], "little", signed=width > 1) for i in range(0, len(data), width)]
                delta = max(abs(a-b) for a,b in zip(samples(original), samples(modified)))
                require(delta <= 3, f"LSB=2 must change PCM samples by <=3, actual maximum delta={delta}")
                return {"maxSampleDelta": delta}
            self.case(f"PCM {width * 8}-bit true least-significant-bit fidelity", fidelity)

        for kind, carrier, key in (("image", JPG, "carrier"), ("wav", WAV, "wav_carrier")):
            for bits in (1, 2, 4, 8):
                def capacity(kind=kind, carrier=carrier, key=key, bits=bits):
                    result = self.post(f"/api/{kind}/analyze", {"num_lsb": bits, "secret_text" if kind == "image" else "wav_secret_text": TEXT}, {key: (carrier.name, carrier.read_bytes())})
                    stats = result["stats"]
                    if kind == "image":
                        expected = stats["width"] * stats["height"] * stats["channels"] * bits // 8 - stats["tagBytes"]
                    else:
                        expected = stats["frameCount"] * stats["channels"] * bits // 8
                    require(stats["capacityBytes"] == expected, "Capacity formula mismatch")
                    require(stats["payloadBytes"] == len(TEXT.encode()) and stats["fits"], "Text capacity mismatch")
                    return stats
                self.case(f"{kind} capacity LSB={bits}", capacity)
            self.case(f"{kind} missing carrier rejected", lambda kind=kind: self.post(f"/api/{kind}/embed", {"secret_text": TEXT}, expected=400))
            self.case(f"{kind} empty secret rejected", lambda kind=kind, carrier=carrier, key=key: self.post(f"/api/{kind}/embed", {}, {key: (carrier.name, carrier.read_bytes())}, 400))
            self.case(f"{kind} missing payload file rejected", lambda kind=kind, carrier=carrier, key=key: self.post(f"/api/{kind}/embed", {"payload_mode": "file"}, {key: (carrier.name, carrier.read_bytes())}, 400))
            self.case(f"{kind} invalid carrier rejected", lambda kind=kind, key=key: self.post(f"/api/{kind}/embed", {"secret_text": TEXT, "wav_secret_text": TEXT}, {key: ("bad.bin", b"not media")}, 400))
            video = next((RESOURCES / "downloads").glob("*.mp4"))
            self.case(f"{kind} original MP4 exceeds carrier capacity", lambda kind=kind, key=key, carrier=carrier: self.post(f"/api/{kind}/embed", {"payload_mode": "file", "num_lsb": 2}, {key: (carrier.name, carrier.read_bytes()), "secret_file" if kind == "image" else "wav_secret_file": (video.name, video.read_bytes())}, 400))

        for name in ("test_resources/downloads/test.jpg", "output/testing/image_text_lsb2.png"):
            def detection(name=name):
                path = ROOT / name
                response = self.post("/api/image/detect", {"num_lsb": 1}, {"detect_image": (path.name, path.read_bytes())})
                content = base64.b64decode(response["image"].split(",", 1)[1])
                with Image.open(BytesIO(content)) as image:
                    data = np.asarray(image)
                    require(image.size == (2133,1199) and np.array_equal(data[:,:,0], data[:,:,1]) and np.array_equal(data[:,:,1],data[:,:,2]), "Invalid visualization")
                (OUTPUT / (path.stem + "_lsb.png")).write_bytes(content)
                return response["stats"]
            self.case(f"LSB visualization {name}", detection)

        def legacy_image():
            image = hide_message_in_image(webui.open_image(JPG.read_bytes()), b"legacy compatible text", 2)
            buf = BytesIO()
            image.save(buf, "PNG")
            result = self.post("/api/image/extract", {"num_lsb": 2}, {"stego": ("legacy.png", buf.getvalue())})
            require(base64.b64decode(result["dataBase64"]) == b"legacy compatible text" and result["stats"]["legacyPayload"], "Legacy image recovery failed")
        self.case("Legacy image payload", legacy_image)

        def legacy_wav():
            with wave.open(str(WAV), "rb") as sound:
                params, frames = sound.getparams(), sound.readframes(sound.getnframes())
            secret = b"legacy wav payload"
            buf = BytesIO()
            with wave.open(buf, "wb") as sound:
                sound.setparams(params)
                sound.writeframes(lsb_interleave_bytes(frames, secret, 2, byte_depth=params.sampwidth))
            result = self.post("/api/wav/extract", {"num_lsb": 2, "recover_bytes": len(secret)}, {"wav_stego": ("legacy.wav", buf.getvalue())})
            require(base64.b64decode(result["dataBase64"]) == secret and not result["stats"]["usedAutoLength"], "Manual WAV recovery failed")
        self.case("Legacy WAV manual length", legacy_wav)
        self.case("Original WAV requires manual length", lambda: self.post("/api/wav/extract", {"num_lsb": 2}, {"wav_stego": (WAV.name, WAV.read_bytes())}, 400))
        self.case("Wrong image LSB rejected", lambda: self.post("/api/image/extract", {"num_lsb": 1}, {"stego": ("encoded.png", (OUTPUT / "image_text_lsb2.png").read_bytes())}, 400))
        self.case("Wrong WAV LSB rejected", lambda: self.post("/api/wav/extract", {"num_lsb": 1}, {"wav_stego": ("encoded.wav", (OUTPUT / "audio_text_lsb2.wav").read_bytes())}, 400))

        for kind, key in (("image", "carrier"), ("wav", "wav_carrier")):
            self.case(f"{kind} analyze truncated media returns JSON 400", lambda kind=kind,key=key: self.post(f"/api/{kind}/analyze", files={key: ("truncated.wav" if kind == "wav" else "truncated.jpg", b"RIFF")}, expected=400))
        def tiny_wav():
            buf = BytesIO()
            with wave.open(buf,"wb") as sound:
                sound.setparams((1,1,8000,0,"NONE","not compressed"))
                sound.writeframes(bytes(16))
            result = self.post("/api/wav/extract", {"num_lsb": 1, "recover_bytes": 1}, {"wav_stego": ("tiny.wav", buf.getvalue())})
            require(base64.b64decode(result["dataBase64"]) == b"\x00", "Tiny WAV manual extraction failed")
        self.case("Tiny WAV manual extraction without auto header", tiny_wav)

        def exact_capacity():
            with Image.open(JPG) as source:
                image = source.resize((64,64)).convert("RGB")
            buf=BytesIO()
            image.save(buf,"PNG")
            capacity=webui.analyze_image(buf.getvalue(),2)["capacityBytes"]
            overhead=webui.stored_payload_size(0,"capacity.bin",False)
            secret=b"C"*(capacity-overhead)
            encoded,_=webui.embed_image_secret(buf.getvalue(),secret,2,6,"capacity.bin",False)
            require(webui.extract_image_secret(encoded,2)[0] == secret, "Exact-capacity image failed")
            try:
                webui.embed_image_secret(buf.getvalue(),secret+b"C",2,6,"capacity.bin",False)
            except ValueError:
                return {"capacityBytes":capacity,"secretBytes":len(secret)}
            raise AssertionError("Capacity+1 must fail")
        self.case("Exact image capacity and one-byte overflow", exact_capacity)

    def finish(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        report={"executedAt":datetime.now().isoformat(timespec="seconds"),"python":sys.version,"results":self.results,"imageMetrics":self.metrics}
        report["summary"]={"total":len(self.results),"passed":sum(r["status"]=="PASS" for r in self.results),"failed":sum(r["status"]=="FAIL" for r in self.results)}
        (OUTPUT / "test_results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report["summary"]),flush=True)


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True,exist_ok=True)
    suite=Suite()
    try:
        suite.run()
    finally:
        suite.finish()
    raise SystemExit(any(result["status"]=="FAIL" for result in suite.results))
