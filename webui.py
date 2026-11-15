from __future__ import annotations

import base64
import json
import mimetypes
import wave
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, cast
from urllib.parse import unquote, urlparse

from PIL import Image, UnidentifiedImageError

from stego_lsb.LSBSteg import (
    bytes_in_max_file_size,
    hide_message_in_image,
    max_bits_to_hide,
    recover_message_from_image,
)
from stego_lsb.bit_manipulation import lsb_deinterleave_bytes, lsb_interleave_bytes

BASE_DIR = Path(__file__).resolve().parent
HTML_DIR = BASE_DIR / "html"
HOST = "127.0.0.1"
PORT = 9001
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
WAV_HEADER_MAGIC = b"SWUI"
WAV_HEADER_SIZE = 8
PAYLOAD_MAGIC = b"SWMD"
PAYLOAD_HEADER_SIZE = 2
MAX_PAYLOAD_METADATA_BYTES = 4096


@dataclass
class UploadFile:
    filename: str
    content: bytes


@dataclass
class PayloadMetadata:
    filename: str
    mime_type: str
    is_text: bool
    legacy: bool = False


def clamp_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def parse_multipart(headers: Any, body: bytes) -> tuple[dict[str, str], dict[str, UploadFile]]:
    content_type = headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        raise ValueError("请求类型必须是 multipart/form-data")

    raw_message = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1")
        + body
    )
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    if not message.is_multipart():
        raise ValueError("上传数据格式不正确")

    fields: dict[str, str] = {}
    files: dict[str, UploadFile] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            files[name] = UploadFile(filename=Path(filename).name, content=payload)
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")
    return fields, files


def encode_data_url(mime_type: str, payload: bytes) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


def safe_output_name(filename: str, fallback: str, suffix: str | None = None) -> str:
    stem = Path(filename).stem.strip() if filename else ""
    if not stem:
        stem = Path(fallback).stem
    clean = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    extension = suffix or Path(filename).suffix or Path(fallback).suffix
    return f"{clean}{extension}"


def build_payload_metadata(filename: str, is_text: bool) -> PayloadMetadata:
    fallback = "secret.txt" if is_text else "payload.bin"
    safe_name = safe_output_name(filename, fallback)
    mime_type = "text/plain; charset=utf-8" if is_text else (
        mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    )
    return PayloadMetadata(filename=safe_name, mime_type=mime_type, is_text=is_text)


def pack_payload(secret_bytes: bytes, filename: str, is_text: bool) -> tuple[bytes, PayloadMetadata]:
    metadata = build_payload_metadata(filename, is_text)
    metadata_bytes = json.dumps(
        {
            "filename": metadata.filename,
            "mimeType": metadata.mime_type,
            "isText": metadata.is_text,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(metadata_bytes) > MAX_PAYLOAD_METADATA_BYTES:
        raise ValueError("载荷文件名或类型信息过长")
    return PAYLOAD_MAGIC + len(metadata_bytes).to_bytes(PAYLOAD_HEADER_SIZE, "big") + metadata_bytes + secret_bytes, metadata


def stored_payload_size(payload_size: int, filename: str, is_text: bool) -> int:
    metadata = build_payload_metadata(filename, is_text)
    metadata_bytes = json.dumps(
        {
            "filename": metadata.filename,
            "mimeType": metadata.mime_type,
            "isText": metadata.is_text,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return PAYLOAD_HEADER_SIZE + len(PAYLOAD_MAGIC) + len(metadata_bytes) + payload_size


def unpack_payload(payload: bytes) -> tuple[bytes, PayloadMetadata]:
    fallback = PayloadMetadata(
        filename="recovered_payload.bin",
        mime_type="application/octet-stream",
        is_text=False,
        legacy=True,
    )
    prefix_size = len(PAYLOAD_MAGIC) + PAYLOAD_HEADER_SIZE
    if not payload.startswith(PAYLOAD_MAGIC) or len(payload) < prefix_size:
        return payload, fallback

    metadata_size = int.from_bytes(payload[len(PAYLOAD_MAGIC):prefix_size], "big")
    metadata_end = prefix_size + metadata_size
    if metadata_size <= 0 or metadata_size > MAX_PAYLOAD_METADATA_BYTES or metadata_end > len(payload):
        return payload, fallback
    try:
        raw_metadata = json.loads(payload[prefix_size:metadata_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return payload, fallback
    if not isinstance(raw_metadata, dict):
        return payload, fallback

    is_text = bool(raw_metadata.get("isText", False))
    metadata = build_payload_metadata(str(raw_metadata.get("filename", "")), is_text)
    return payload[metadata_end:], metadata


def decode_payload(payload: bytes) -> tuple[str, bool]:
    try:
        return payload.decode("utf-8"), True
    except UnicodeDecodeError:
        return payload.decode("utf-8", errors="replace"), False


def select_secret_payload(
    fields: dict[str, str],
    files: dict[str, UploadFile],
    text_field: str = "secret_text",
    file_field: str = "secret_file",
) -> tuple[bytes, str, bool]:
    mode = fields.get("payload_mode", "text")
    if mode == "file":
        upload = files.get(file_field)
        if upload is None or not upload.content:
            raise ValueError("请选择需要隐藏的文件")
        return upload.content, upload.filename, False

    text = fields.get(text_field, "")
    if not text:
        raise ValueError("请输入需要隐藏的文本")
    return text.encode("utf-8"), "secret.txt", True


def open_image(image_bytes: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(image_bytes))
        image.load()
    except UnidentifiedImageError as exc:
        raise ValueError("上传文件不是可识别的图片") from exc
    except OSError as exc:
        raise ValueError("图片文件无法读取") from exc

    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    return image


def image_capacity(image: Image.Image, num_lsb: int) -> tuple[int, int, int]:
    channels = len(image.getbands())
    tag_bytes = bytes_in_max_file_size(image, num_lsb, channels)
    raw_capacity = max_bits_to_hide(image, num_lsb, channels) // 8
    return max(0, raw_capacity - tag_bytes), tag_bytes, channels


def analyze_image(
    image_bytes: bytes,
    num_lsb: int,
    payload_size: int = 0,
    stored_size: int = 0,
) -> dict[str, int | bool]:
    image = open_image(image_bytes)
    capacity_bytes, tag_bytes, channels = image_capacity(image, num_lsb)
    stored_bytes = stored_size or payload_size
    return {
        "width": image.width,
        "height": image.height,
        "channels": channels,
        "numLsb": num_lsb,
        "capacityBytes": capacity_bytes,
        "tagBytes": tag_bytes,
        "payloadBytes": payload_size,
        "storedBytes": stored_bytes,
        "fits": stored_bytes <= capacity_bytes,
    }


def embed_image_secret(
    carrier_bytes: bytes,
    secret_bytes: bytes,
    num_lsb: int,
    compression_level: int,
    secret_name: str = "secret.txt",
    secret_is_text: bool = False,
) -> tuple[bytes, dict[str, int | bool]]:
    if not secret_bytes:
        raise ValueError("隐藏内容不能为空")

    image = open_image(carrier_bytes)
    payload, _ = pack_payload(secret_bytes, secret_name, secret_is_text)
    stats = analyze_image(carrier_bytes, num_lsb, len(secret_bytes), len(payload))
    if not stats["fits"]:
        raise ValueError(
            f"隐藏内容为 {len(payload)} 字节，当前图片在 {num_lsb} LSB 下最多可隐藏 "
            f"{stats['capacityBytes']} 字节"
        )

    stego_image = hide_message_in_image(image.copy(), payload, num_lsb)
    output = BytesIO()
    stego_image.save(output, format="PNG", compress_level=compression_level)
    encoded_bytes = output.getvalue()
    stats["outputBytes"] = len(encoded_bytes)
    return encoded_bytes, stats


def extract_image_secret(stego_bytes: bytes, num_lsb: int) -> tuple[bytes, str, dict[str, Any]]:
    image = open_image(stego_bytes)
    stored_payload = recover_message_from_image(image, num_lsb)
    recovered, metadata = unpack_payload(stored_payload)
    text, decoded_as_text = decode_payload(recovered)
    stats = analyze_image(stego_bytes, num_lsb, len(recovered), len(stored_payload))
    stats["isUtf8Text"] = metadata.is_text if not metadata.legacy else decoded_as_text
    stats["downloadName"] = metadata.filename
    stats["mimeType"] = metadata.mime_type
    stats["legacyPayload"] = metadata.legacy
    return recovered, text, stats


def detect_image_lsb(image_bytes: bytes, num_lsb: int) -> tuple[bytes, dict[str, int]]:
    image = open_image(image_bytes).convert("RGB")
    mask = (1 << num_lsb) - 1
    image_data = cast(Iterable[tuple[int, int, int]], image.getdata())
    color_data = [
        (255 * ((rgb[0] & mask) + (rgb[1] & mask) + (rgb[2] & mask)) // (3 * mask),) * 3
        for rgb in image_data
    ]
    output_image = Image.new("RGB", image.size)
    output_image.putdata(color_data)
    output = BytesIO()
    output_image.save(output, format="PNG")
    return output.getvalue(), {
        "width": image.width,
        "height": image.height,
        "channels": 3,
        "numLsb": num_lsb,
        "outputBytes": output.tell(),
    }


def read_wav(wav_bytes: bytes) -> tuple[wave._wave_params, int, int, int, int, int, bytes]:
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as sound:
            params = sound.getparams()
            channels = sound.getnchannels()
            sample_width = sound.getsampwidth()
            frame_rate = sound.getframerate()
            frame_count = sound.getnframes()
            frames = sound.readframes(frame_count)
    except (wave.Error, EOFError) as exc:
        raise ValueError("上传文件不是有效的 WAV 音频") from exc

    if sample_width < 1 or sample_width > 4:
        raise ValueError("当前 WAV 位深不受支持")
    return params, channels, sample_width, frame_rate, frame_count, frame_count * channels, frames


def analyze_wav(
    wav_bytes: bytes,
    num_lsb: int,
    payload_size: int = 0,
    stored_payload_size: int = 0,
) -> dict[str, int | float | bool]:
    _, channels, sample_width, frame_rate, frame_count, sample_count, _ = read_wav(wav_bytes)
    capacity_bytes = (sample_count * num_lsb) // 8
    payload_bytes = stored_payload_size or payload_size
    stored_bytes = payload_bytes + WAV_HEADER_SIZE if payload_bytes else 0
    return {
        "channels": channels,
        "sampleWidth": sample_width,
        "frameRate": frame_rate,
        "frameCount": frame_count,
        "durationSeconds": round(frame_count / frame_rate, 3) if frame_rate else 0,
        "numLsb": num_lsb,
        "capacityBytes": capacity_bytes,
        "payloadBytes": payload_size,
        "storedBytes": stored_bytes,
        "fits": stored_bytes <= capacity_bytes,
    }


def embed_wav_secret(
    wav_bytes: bytes,
    secret_bytes: bytes,
    num_lsb: int,
    secret_name: str = "secret.txt",
    secret_is_text: bool = False,
) -> tuple[bytes, dict[str, int | float | bool]]:
    if not secret_bytes:
        raise ValueError("隐藏内容不能为空")

    params, _, sample_width, _, _, _, frames = read_wav(wav_bytes)
    stored_payload, _ = pack_payload(secret_bytes, secret_name, secret_is_text)
    stats = analyze_wav(wav_bytes, num_lsb, len(secret_bytes), len(stored_payload))
    if not stats["fits"]:
        raise ValueError(
            f"隐藏内容为 {len(stored_payload)} 字节，当前 WAV 在 {num_lsb} LSB 下最多可隐藏 "
            f"{stats['capacityBytes']} 字节"
        )

    payload = WAV_HEADER_MAGIC + len(stored_payload).to_bytes(4, "big") + stored_payload
    stego_frames = lsb_interleave_bytes(frames, payload, num_lsb, byte_depth=sample_width)
    output = BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setparams(params)
        sound.writeframes(stego_frames)
    encoded_bytes = output.getvalue()
    stats["outputBytes"] = len(encoded_bytes)
    return encoded_bytes, stats


def extract_wav_secret(
    wav_bytes: bytes,
    num_lsb: int,
    requested_bytes: int | None,
) -> tuple[bytes, str, dict[str, Any]]:
    _, _, sample_width, _, _, sample_count, frames = read_wav(wav_bytes)
    capacity_bytes = (sample_count * num_lsb) // 8
    if capacity_bytes < WAV_HEADER_SIZE and requested_bytes is None:
        raise ValueError("音频容量过小，无法自动读取长度信息")

    header = (bytes(lsb_deinterleave_bytes(frames, WAV_HEADER_SIZE * 8, num_lsb, byte_depth=sample_width))
              if capacity_bytes >= WAV_HEADER_SIZE else b"")
    used_auto_length = header[:4] == WAV_HEADER_MAGIC
    if used_auto_length:
        payload_size = int.from_bytes(header[4:8], "big")
        bytes_to_recover = WAV_HEADER_SIZE + payload_size
        if bytes_to_recover > capacity_bytes:
            raise ValueError("音频中的长度信息超过可恢复范围")
        stored_payload = bytes(
            lsb_deinterleave_bytes(frames, bytes_to_recover * 8, num_lsb, byte_depth=sample_width)
        )[WAV_HEADER_SIZE:]
    else:
        if requested_bytes is None or requested_bytes <= 0:
            raise ValueError("该 WAV 未包含 WebUI 长度标记，请输入需要恢复的字节数")
        if requested_bytes > capacity_bytes:
            raise ValueError(f"恢复字节数不能超过 {capacity_bytes}")
        stored_payload = bytes(
            lsb_deinterleave_bytes(frames, requested_bytes * 8, num_lsb, byte_depth=sample_width)
        )

    recovered, metadata = unpack_payload(stored_payload)
    text, decoded_as_text = decode_payload(recovered)
    stats = analyze_wav(wav_bytes, num_lsb, len(recovered), len(stored_payload))
    stats["isUtf8Text"] = metadata.is_text if not metadata.legacy else decoded_as_text
    stats["usedAutoLength"] = used_auto_length
    stats["downloadName"] = metadata.filename
    stats["mimeType"] = metadata.mime_type
    stats["legacyPayload"] = metadata.legacy
    return recovered, text, stats


class StegoWebHandler(BaseHTTPRequestHandler):
    server_version = "StegoWebUI/2.0"

    def do_GET(self) -> None:
        parsed_path = unquote(urlparse(self.path).path)
        if parsed_path == "/health":
            self.send_json(200, {"ok": True})
            return
        if parsed_path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed_path == "/":
            parsed_path = "/index.html"
        self.serve_static(parsed_path)

    def do_POST(self) -> None:
        routes = {
            "/api/image/embed": self.handle_image_embed,
            "/api/image/extract": self.handle_image_extract,
            "/api/image/analyze": self.handle_image_analyze,
            "/api/image/detect": self.handle_image_detect,
            "/api/wav/embed": self.handle_wav_embed,
            "/api/wav/extract": self.handle_wav_extract,
            "/api/wav/analyze": self.handle_wav_analyze,
            "/api/embed": self.handle_image_embed,
            "/api/extract": self.handle_image_extract,
        }
        handler = routes.get(unquote(urlparse(self.path).path))
        if handler is None:
            self.send_json(404, {"ok": False, "error": "接口不存在"})
            return
        handler()

    def handle_image_embed(self) -> None:
        try:
            fields, files = self.read_form()
            carrier = self.required_file(files, "carrier", "请选择载体图片")
            secret, secret_name, secret_is_text = select_secret_payload(fields, files)
            num_lsb = clamp_int(fields.get("num_lsb"), 2, 1, 8)
            compression = clamp_int(fields.get("compression"), 6, 0, 9)
            encoded, stats = embed_image_secret(
                carrier.content, secret, num_lsb, compression, secret_name, secret_is_text
            )
            self.send_json(
                200,
                {
                    "ok": True,
                    "filename": safe_output_name(carrier.filename, "stego_image.png", ".png"),
                    "image": encode_data_url("image/png", encoded),
                    "sourceName": carrier.filename,
                    "secretName": secret_name,
                    "secretIsText": secret_is_text,
                    "stats": stats,
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": f"图片嵌入失败：{exc}"})

    def handle_image_extract(self) -> None:
        try:
            fields, files = self.read_form()
            stego = self.required_file(files, "stego", "请选择隐写图片")
            num_lsb = clamp_int(fields.get("num_lsb"), 2, 1, 8)
            recovered, text, stats = extract_image_secret(stego.content, num_lsb)
            self.send_json(
                200,
                {
                    "ok": True,
                    "text": text,
                    "dataBase64": base64.b64encode(recovered).decode("ascii"),
                    "downloadName": stats["downloadName"],
                    "mimeType": stats["mimeType"],
                    "stats": stats,
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": f"图片提取失败：{exc}"})

    def handle_image_analyze(self) -> None:
        try:
            fields, files = self.read_form()
            image = files.get("carrier") or files.get("image")
            if image is None or not image.content:
                raise ValueError("请选择图片")
            num_lsb = clamp_int(fields.get("num_lsb"), 2, 1, 8)
            payload_size, stored_size = self.payload_sizes(fields, files)
            self.send_json(
                200,
                {"ok": True, "stats": analyze_image(image.content, num_lsb, payload_size, stored_size)},
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def handle_image_detect(self) -> None:
        try:
            fields, files = self.read_form()
            image = self.required_file(files, "detect_image", "请选择需要检测的图片")
            num_lsb = clamp_int(fields.get("num_lsb"), 1, 1, 8)
            detected, stats = detect_image_lsb(image.content, num_lsb)
            self.send_json(
                200,
                {
                    "ok": True,
                    "filename": safe_output_name(image.filename, "lsb_view.png", "_lsb.png"),
                    "image": encode_data_url("image/png", detected),
                    "stats": stats,
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": f"最低位可视化失败：{exc}"})

    def handle_wav_embed(self) -> None:
        try:
            fields, files = self.read_form()
            carrier = self.required_file(files, "wav_carrier", "请选择 WAV 音频")
            secret, secret_name, secret_is_text = select_secret_payload(fields, files, "wav_secret_text", "wav_secret_file")
            num_lsb = clamp_int(fields.get("num_lsb"), 2, 1, 8)
            encoded, stats = embed_wav_secret(
                carrier.content, secret, num_lsb, secret_name, secret_is_text
            )
            self.send_json(
                200,
                {
                    "ok": True,
                    "filename": safe_output_name(carrier.filename, "stego_audio.wav", ".wav"),
                    "audio": encode_data_url("audio/wav", encoded),
                    "sourceName": carrier.filename,
                    "secretName": secret_name,
                    "secretIsText": secret_is_text,
                    "stats": stats,
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": f"WAV 嵌入失败：{exc}"})

    def handle_wav_extract(self) -> None:
        try:
            fields, files = self.read_form()
            stego = self.required_file(files, "wav_stego", "请选择隐写 WAV 音频")
            num_lsb = clamp_int(fields.get("num_lsb"), 2, 1, 8)
            requested = fields.get("recover_bytes", "").strip()
            requested_bytes = int(requested) if requested else None
            recovered, text, stats = extract_wav_secret(stego.content, num_lsb, requested_bytes)
            self.send_json(
                200,
                {
                    "ok": True,
                    "text": text,
                    "dataBase64": base64.b64encode(recovered).decode("ascii"),
                    "downloadName": stats["downloadName"],
                    "mimeType": stats["mimeType"],
                    "stats": stats,
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": f"WAV 提取失败：{exc}"})

    def handle_wav_analyze(self) -> None:
        try:
            fields, files = self.read_form()
            audio = self.required_file(files, "wav_carrier", "请选择 WAV 音频")
            num_lsb = clamp_int(fields.get("num_lsb"), 2, 1, 8)
            payload_size, stored_size = self.payload_sizes(
                fields, files, "wav_secret_text", "wav_secret_file"
            )
            self.send_json(
                200,
                {"ok": True, "stats": analyze_wav(audio.content, num_lsb, payload_size, stored_size)},
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def payload_sizes(
        self,
        fields: dict[str, str],
        files: dict[str, UploadFile],
        text_field: str = "secret_text",
        file_field: str = "secret_file",
    ) -> tuple[int, int]:
        mode = fields.get("payload_mode", "text")
        if mode == "file":
            upload = files.get(file_field)
            if upload is None:
                return 0, 0
            raw_size = len(upload.content)
            return raw_size, stored_payload_size(raw_size, upload.filename, False)
        raw_size = len(fields.get(text_field, "").encode("utf-8"))
        return raw_size, stored_payload_size(raw_size, "secret.txt", True) if raw_size else 0

    def required_file(self, files: dict[str, UploadFile], name: str, message: str) -> UploadFile:
        upload = files.get(name)
        if upload is None or not upload.content:
            raise ValueError(message)
        return upload

    def read_form(self) -> tuple[dict[str, str], dict[str, UploadFile]]:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            raise ValueError("缺少 Content-Length")
        try:
            content_length = int(length_header)
        except ValueError as exc:
            raise ValueError("Content-Length 不正确") from exc
        if content_length > MAX_UPLOAD_BYTES:
            raise ValueError("上传文件过大")

        body = self.rfile.read(content_length)
        return parse_multipart(self.headers, body)

    def serve_static(self, request_path: str) -> None:
        target = (HTML_DIR / request_path.lstrip("/")).resolve()
        html_root = HTML_DIR.resolve()
        if html_root not in target.parents and target != html_root:
            self.send_json(403, {"ok": False, "error": "禁止访问"})
            return
        if not target.is_file():
            self.send_json(404, {"ok": False, "error": "文件不存在"})
            return

        content = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), StegoWebHandler)
    print(f"图像隐写系统 WebUI: http://{HOST}:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
