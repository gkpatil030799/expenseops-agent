from __future__ import annotations

import io
import os
import time
import warnings
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError

ReceiptMediaClass = Literal["image", "pdf"]

_SUPPORTED_DECLARED_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "application/octet-stream",
        "binary/octet-stream",
        "",
    }
)
_IMAGE_FORMAT_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_MAX_IMAGE_PIXELS = 50_000_000
_EXIF_ORIENTATION = 274


class ReceiptArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class ReceiptArtifact:
    """One validated receipt input shared by Telegram, Gmail, web, and direct tests.

    The original bytes live only for the duration of ingestion. ExpenseOps persists
    the existing SHA-256 fingerprint and parsed facts, not the sensitive image.
    """

    source: str
    source_external_id: str
    filename: str
    declared_mime_type: str
    media_type: str
    media_class: ReceiptMediaClass
    original_content: bytes
    normalized_content: bytes
    size_bytes: int
    page_count: int | None
    width: int | None
    height: int | None
    orientation_corrected: bool
    normalization_latency_ms: int


def build_receipt_artifact(
    *,
    source: str,
    source_external_id: str,
    content: bytes,
    mime_type: str,
    filename: str,
    max_bytes: int,
) -> ReceiptArtifact:
    started = time.monotonic()
    if not content:
        raise ReceiptArtifactError("receipt_image_empty")
    if len(content) > max_bytes:
        raise ReceiptArtifactError("receipt_attachment_too_large")

    declared = mime_type.casefold().split(";", 1)[0].strip()
    if declared not in _SUPPORTED_DECLARED_TYPES:
        raise ReceiptArtifactError("unsupported_receipt_type")
    safe_filename = _safe_filename(filename)

    if content.startswith(b"%PDF-"):
        if declared not in {
            "",
            "application/pdf",
            "application/octet-stream",
            "binary/octet-stream",
        }:
            raise ReceiptArtifactError("receipt_media_type_mismatch")
        return ReceiptArtifact(
            source=source,
            source_external_id=source_external_id,
            filename=safe_filename or "receipt.pdf",
            declared_mime_type=declared,
            media_type="application/pdf",
            media_class="pdf",
            original_content=content,
            normalized_content=content,
            size_bytes=len(content),
            page_count=None,
            width=None,
            height=None,
            orientation_corrected=False,
            normalization_latency_ms=_elapsed_ms(started),
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as opened:
                image_format = str(opened.format or "").upper()
                if image_format not in _IMAGE_FORMAT_MIME:
                    raise ReceiptArtifactError("unsupported_receipt_type")
                actual_mime = _IMAGE_FORMAT_MIME[image_format]
                if declared not in {
                    "",
                    actual_mime,
                    "image/jpg" if actual_mime == "image/jpeg" else actual_mime,
                    "application/octet-stream",
                    "binary/octet-stream",
                }:
                    raise ReceiptArtifactError("receipt_media_type_mismatch")
                width, height = opened.size
                if width < 32 or height < 32:
                    raise ReceiptArtifactError("receipt_image_too_small")
                if width * height > _MAX_IMAGE_PIXELS:
                    raise ReceiptArtifactError("receipt_image_pixel_limit")
                orientation = int(opened.getexif().get(_EXIF_ORIENTATION, 1) or 1)
                opened.load()
                if orientation in {2, 3, 4, 5, 6, 7, 8}:
                    normalized = ImageOps.exif_transpose(opened)
                    normalized_content, actual_mime = _encode_image(normalized, image_format)
                    width, height = normalized.size
                    corrected = True
                else:
                    normalized_content = content
                    corrected = False
    except ReceiptArtifactError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ReceiptArtifactError("receipt_image_pixel_limit") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ReceiptArtifactError("receipt_image_corrupt") from None

    return ReceiptArtifact(
        source=source,
        source_external_id=source_external_id,
        filename=safe_filename or f"receipt.{_extension(actual_mime)}",
        declared_mime_type=declared,
        media_type=actual_mime,
        media_class="image",
        original_content=content,
        normalized_content=normalized_content,
        size_bytes=len(content),
        page_count=1,
        width=width,
        height=height,
        orientation_corrected=corrected,
        normalization_latency_ms=_elapsed_ms(started),
    )


def _encode_image(image: Image.Image, image_format: str) -> tuple[bytes, str]:
    output = io.BytesIO()
    if image_format == "JPEG":
        converted = image.convert("RGB")
        converted.save(output, format="JPEG", quality=95, optimize=True)
        return output.getvalue(), "image/jpeg"
    if image_format == "PNG":
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), "image/png"
    image.save(output, format="WEBP", quality=95, method=4)
    return output.getvalue(), "image/webp"


def _safe_filename(value: str) -> str:
    name = os.path.basename(value.replace("\\", "/")).strip()
    return "".join(character for character in name if character.isprintable())[:255]


def _extension(mime_type: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime_type]


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
