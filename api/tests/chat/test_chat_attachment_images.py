"""
Mobile image attachments must reach the LLM vision models.

A phone reports what it knows — an iPhone gallery screenshot is `image/heic`,
some gallery apps send `image/bmp`, a share can carry no type at all — and none
of those are formats the vision APIs decode. The attachment was either rejected
when the message was stored ("type not allowed") or dropped from the model
request. These tests pin the behaviour:

* `db.ChatAttachment` accepts any `image/*` (the message is stored),
* the REAL format is sniffed from the payload, not from the metadata,
* anything the providers cannot decode is re-encoded as PNG/JPEG (Pillow),
* non-images (voice notes) never travel as an `image_url` part.
"""
import base64
import io

import pytest

from codx.junior.chat.chat_prompt_builder import (
    ChatPromptBuilder,
    sniff_image_format,
)
from codx.junior.db import ChatAttachment


def attachment(file_name: str, file_type: str, data: bytes) -> ChatAttachment:
    """Build a stored attachment (server side model, validators included)."""
    return ChatAttachment(
        file_name=file_name,
        file_type=file_type,
        file_size=len(data),
        base64_data=base64.b64encode(data).decode("ascii"),
    )


def prepared(attachment_obj) -> dict:
    """Run the prompt builder preparation for a single attachment."""
    return ChatPromptBuilder._prepare_attachment({
        "role": "user",
        "label": "chat turn 1 · user",
        "attachment": attachment_obj,
    })


def image_bytes(image_format: str, mode: str = "RGB") -> bytes:
    """Render a tiny image with Pillow and return its encoded bytes."""
    Image = pytest.importorskip("PIL.Image")
    image = Image.new(mode, (8, 6), (255, 0, 0) if mode == "RGB" else (255, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------
def test_sniff_returns_the_real_format_of_every_supported_image():
    assert sniff_image_format(base64.b64encode(image_bytes("PNG")).decode()) == "png"
    assert sniff_image_format(base64.b64encode(image_bytes("JPEG")).decode()) == "jpeg"
    assert sniff_image_format(base64.b64encode(image_bytes("GIF")).decode()) == "gif"
    assert sniff_image_format(base64.b64encode(image_bytes("WEBP")).decode()) == "webp"
    assert sniff_image_format(base64.b64encode(image_bytes("BMP")).decode()) == "bmp"


def test_sniff_detects_heic_from_the_ftyp_box():
    # `ftyp` + brand, the header every HEIC/HEIF file starts with.
    heic_head = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
    assert sniff_image_format(base64.b64encode(heic_head).decode()) == "heif"


def test_sniff_ignores_payloads_that_are_not_images():
    assert sniff_image_format("") == ""
    assert sniff_image_format(base64.b64encode(b"%PDF-1.7").decode()) == ""


# ---------------------------------------------------------------------------
# Attachment type validation (storing the message)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("file_type", [
    "image/heic", "image/heif", "image/bmp", "image/tiff", "image/avif",
])
def test_mobile_image_types_are_stored(file_type):
    """A phone screenshot must not make the whole message fail."""
    stored = attachment(f"IMG_{file_type.split('/')[1].upper()}.bin", file_type, b"data")
    assert stored.file_type == file_type


def test_non_image_attachment_types_are_still_validated():
    with pytest.raises(ValueError):
        attachment("notes.txt", "text/plain", b"hello")


# ---------------------------------------------------------------------------
# Outgoing content
# ---------------------------------------------------------------------------
def test_heic_declared_screenshot_with_png_bytes_is_sent_as_png():
    """
    The payload decides.

    Phones declare `image/heic` while the bytes are plain PNG more often than
    one would think (the OS transcodes on share). Sending the declared type is
    what makes a provider show "[Unsupported Image]".
    """
    result = prepared(attachment("IMG_0001.HEIC", "image/heic", image_bytes("PNG")))

    assert result["part"] is not None
    assert result["media_type"] == "image/png"
    assert result["part"]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "detected image/png instead of image/heic" in result["note"]


def test_heic_declared_photo_with_jpeg_bytes_is_sent_as_jpeg():
    result = prepared(attachment("IMG_0002.HEIC", "image/heic", image_bytes("JPEG")))

    assert result["media_type"] == "image/jpeg"
    assert result["part"]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_bmp_is_converted_to_a_vision_format():
    """A format no provider decodes is re-encoded, not dropped."""
    result = prepared(attachment("shot.bmp", "image/bmp", image_bytes("BMP")))

    assert result["part"] is not None
    assert result["media_type"] in ("image/png", "image/jpeg")
    assert result["part"]["image_url"]["url"].startswith(f"data:{result['media_type']};base64,")
    assert "re-encoded as" in result["note"]


def test_png_is_sent_untouched():
    data = image_bytes("PNG")
    result = prepared(attachment("shot.png", "image/png", data))

    assert result["media_type"] == "image/png"
    assert result["part"]["image_url"]["url"].endswith(
        base64.b64encode(data).decode("ascii")
    )
    assert "re-encoded" not in result["note"]


def test_voice_note_is_never_sent_as_an_image():
    result = prepared(attachment("voice-note.webm", "audio/webm", b"webm-bytes"))

    assert result["part"] is None
    assert "is not an image" in result["note"]


def test_undecodable_image_is_reported_instead_of_breaking_the_request():
    """An image Pillow cannot read (SVG, broken upload) only travels as text."""
    result = prepared(attachment("logo.svg", "image/svg+xml", b"<svg></svg>"))

    assert result["part"] is None
    assert "not sent to the model" in result["note"]


def test_mobile_screenshot_ends_up_as_one_image_part():
    """
    Whole pipeline: collected message attachment -> content part + text block.

    The screenshot is declared `image/heic` by the phone; the model request must
    carry it exactly once, as a decodable PNG part.
    """
    from types import SimpleNamespace

    builder = ChatPromptBuilder(
        get_chat_manager=lambda project_id=None: None,
        get_chat_analysis_parents=lambda chat: "",
    )
    message = SimpleNamespace(
        role="user",
        content="what is wrong in this screen?",
        attachments=[attachment("IMG_0003.HEIC", "image/heic", image_bytes("PNG"))],
        hide=False,
        improvement=False,
        doc_id="message-1",
    )
    chat = SimpleNamespace(
        messages=[message],
        parent_id=None,
        ignore_parent_knowledge=True,
        doc_id="chat-1",
    )

    prepared_items = [
        builder._prepare_attachment(item)
        for item in builder.collect_attachments(chat=chat)
    ]
    parts = builder._build_attachment_parts(prepared_items)
    text_block = builder._build_attachment_text_block(prepared_items)

    assert len(parts) == 1
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "IMG_0003.HEIC" in text_block
    assert "sent to the model as image (image/png" in text_block