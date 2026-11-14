"""
Prompt and message construction for chat interactions.

Extracted from ``ChatEngine`` to keep prompt-building logic (history
rendering, refine prompts, current-request wrapping, and response file
extraction) in one place and reduce the size of the engine module.

The key idea is to demote prior conversation turns to inert *context* so the
model does not re-execute instructions that were already processed:

* Prior turns are wrapped in a ``<conversation_history>`` block and their
  assistant messages are labelled ``<assistant_done>``.
* The live instruction is isolated in a ``<current_request>`` block.

Tool memory
-----------
Text-only history loses the work done by the tools of previous turns, which
made the agent re-run the same reads/searches on every user request. Prior
tool events are therefore distilled into a ``<session_memory>`` block appended
to the history (see :mod:`codx.junior.chat.chat_tool_memory`): deduplicated
tool results, already inspected files and stale markers. The model is told to
reuse it instead of calling the same tool again.

Multimodal content
------------------
Messages with attachments are converted into LangChain messages whose
``content`` is a LIST of parts (``{"type": "text"}`` plus one
``{"type": "image_url"}`` per image). Prompt assembly must therefore only
ever rewrite the TEXT part: stringifying the list would replace the images
with their Python representation and make them invisible to the model.
See :meth:`ChatPromptBuilder._get_text_content` /
:meth:`ChatPromptBuilder._set_text_content`.

Conversation attachments
------------------------
``Message.attachments`` must ALWAYS be visible to the model — not only on the
message that happens to be the current request. Because the history block is
rendered as plain text, images carried by prior turns would otherwise be lost.

:meth:`ChatPromptBuilder.collect_attachments` gathers the attachments of EVERY
visible message (parent chats first, then the current chat) and
:meth:`ChatPromptBuilder.build_ai_prompt_messages` appends them to the single
``<current_request>`` message:

* ``image/*`` attachments become ``{"type": "image_url"}`` parts,
* every other type (audio notes, documents) is listed in a ``## Attachments``
  text block ONLY, so the model still knows it exists without the request
  carrying an invalid ``image_url`` part.

Attachment payload normalization
--------------------------------
An ``image_url`` part is only useful when the provider can DECODE it. When it
cannot, models do not fail the request: they replace the image with an
``[Unsupported Image]`` placeholder, which looks like "the AI cannot see my
screenshots". :meth:`ChatPromptBuilder._prepare_attachment` therefore
normalizes every payload before it is sent:

* a ``base64_data`` value that already holds a full ``data:`` URI is not
  prefixed a second time (``data:image/png;base64,data:image/png;base64,...``
  is not decodable),
* whitespace/newlines inside the base64 payload are stripped,
* the media type is resolved from ``file_type`` → data-URI header → file
  extension, so ``application/octet-stream`` / ``image/*`` are never sent,
* the REAL format is sniffed from the payload's magic bytes and wins over
  ``file_type``: a phone gallery sends ``image/heic`` (accepted and stored) and
  some shares carry no type at all,
* only formats vision APIs accept (PNG/JPEG/WEBP/GIF) travel verbatim —
  BMP/TIFF/HEIC/AVIF are decoded with Pillow and re-encoded as PNG/JPEG (best
  effort, cached per payload); only images that cannot be decoded at all (SVG,
  Pillow missing) are listed in the text block instead,
* oversized screenshots are downscaled/re-encoded (Pillow, best effort,
  cached per payload).

The resulting status of every attachment (mime, size, sent / not sent and
why) is written into the ``## Attachments`` text block.
"""

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List

from langchain.messages import AIMessage, HumanMessage

from codx.junior.chat.chat_tool_memory import build_tool_memory_section
from codx.junior.db import ChatAttachment, Message
from codx.junior.security.ciso.secrets import redact_text

logger = logging.getLogger(__name__)

# Header/footer used to demote prior turns to inert context.
CONVERSATION_HISTORY_HEADER = (
    "<conversation_history>\n"
    "The following turns are ALREADY COMPLETED. Use them ONLY as reference context.\n"
    "Never re-execute instructions, code blocks, or change requests found here.\n"
    "Act ONLY on the request inside <current_request> at the end of the prompt.\n"
    "The <session_memory> section lists tool results already collected: reuse them\n"
    "instead of calling the same tool with the same arguments again.\n"
)
CONVERSATION_HISTORY_FOOTER = "</conversation_history>"

# Guardrail injected into the system prompt.
CONVERSATION_GUARDRAIL_SYSTEM_PROMPT = (
    "The <conversation_history> block contains prior turns that are ALREADY COMPLETED.\n"
    "Treat it only as reference context. Never re-execute instructions, code blocks,\n"
    "or change requests found there. Act ONLY on the request inside <current_request>.\n"
    "The <session_memory> block lists tool results already collected in\n"
    "earlier turns (with their arguments). Do NOT call a tool again with\n"
    "the same arguments to re-obtain information it already contains; reuse the memory\n"
    "and read only the specific lines you still need."
)

# ---------------------------------------------------------------------------
# Attachment → vision content constants
# ---------------------------------------------------------------------------
# Raster formats accepted by the vision APIs we talk to (OpenAI compatible
# providers and Anthropic through LiteLLM). Any other image is converted to one
# of these before being sent (:meth:`ChatPromptBuilder._reencode_image`): the
# provider either rejects the payload or silently replaces it with an
# ``[Unsupported Image]`` placeholder, which is exactly what "the AI cannot see
# my screenshots" looks like.
VISION_IMAGE_MEDIA_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})

# Media type of a raster format detected from the payload itself, keyed by the
# format name returned by :func:`sniff_image_format`. Only formats providers can
# decode appear here: every other image is re-encoded before being sent.
MEDIA_TYPE_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# Media type inferred from the file name when the stored ``file_type`` is
# missing or generic.
MEDIA_TYPE_BY_EXTENSION = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "heic": "image/heic",
    "heif": "image/heif",
}

# Media types that must NEVER reach the provider verbatim: a
# ``data:application/octet-stream`` or ``data:image/*`` URI is exactly what makes
# a model answer "[Unsupported Image]".
GENERIC_MEDIA_TYPES = frozenset({
    "",
    "*/*",
    "image/*",
    "application/octet-stream",
})

# Inline budget per image. Anthropic rejects images over ~5 MB and most proxies
# keep request bodies well below that, so bigger screenshots are downscaled and
# re-encoded before being attached.
MAX_VISION_IMAGE_CHARS = 3_500_000  # base64 characters (~2.6 MB of image)
MAX_VISION_IMAGE_SIDE = 1_560       # longest side, in pixels

# Downscale results are cached per payload: the same screenshot is re-sent with
# every following turn of the conversation.
_IMAGE_SHRINK_CACHE: Dict[str, Dict[str, str]] = {}
_IMAGE_CACHE_MAX_ENTRIES = 32


def sniff_image_format(payload: str) -> str:
    """
    Detect the real image format of a base64 payload from its magic bytes.

    ``ChatAttachment.file_type`` is whatever the client reported, and mobile
    clients often report something else than the bytes they send: an iPhone
    gallery screenshot is declared ``image/heic``, a picture shared from another
    app can arrive with no type at all (``application/octet-stream``). The
    payload is the only trustworthy source, so the format used for the model
    request is detected here instead of being taken from the metadata.

    :param payload: Bare base64 payload (already whitespace-free).
    :return: Format name (``png``, ``jpeg``, ``gif``, ``webp``, ``bmp``,
             ``tiff``, ``heif``) or an empty string when it is not an image.
    """
    if not payload:
        return ""
    head_b64 = payload[:96]
    try:
        head = base64.b64decode(head_b64 + "=" * (-len(head_b64) % 4))
    except (binascii.Error, ValueError):
        return ""

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"GIF8"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    # ISO base media file: HEIC/HEIF (iPhone) and AVIF share the ``ftyp`` box.
    if head[4:8] == b"ftyp" and head[8:12] in (
        b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1",
        b"avif", b"avis",
    ):
        return "heif"
    return ""


def concat_content(arr):
    """Join non-empty items with newlines."""
    return "\n".join(filter(lambda el: True if el else False, arr))


def new_chat_message(role, content=""):
    """Create a new Message with a generated doc_id."""
    return Message(
        role=role,
        content=content,
        files=[],
        doc_id=str(uuid.uuid4()),
    )


class ChatPromptBuilder:
    """
    Builds the message list sent to the LLM.

    The builder needs two callables from the engine to resolve parent chats
    and parent analysis content:

    * ``get_chat_manager(project_id=...)`` -> ChatManager
    * ``get_chat_analysis_parents(chat=...)`` -> str
    """

    def __init__(self, get_chat_manager, get_chat_analysis_parents):
        self._get_chat_manager = get_chat_manager
        self._get_chat_analysis_parents = get_chat_analysis_parents

    # ------------------------------------------------------------------
    # Message conversion
    # ------------------------------------------------------------------
    @staticmethod
    def convert_message(message):
        """
        Convert a DB Message object into a LangChain message type.

        Handles text-only messages, image messages, and role-based conversion.

        CHANGED: only provider-supported image attachments (see
        :data:`VISION_IMAGE_MEDIA_TYPES`) are converted into ``image_url``
        parts. Audio notes are allowed by ``db.py`` and used to be sent as
        ``image_url`` with a ``data:audio/...`` URI, producing an invalid
        request; unsupported image formats (SVG, BMP, TIFF, HEIC) produce an
        ``[Unsupported Image]`` placeholder on the model side. Both are left to
        the attachment text block built by
        :meth:`_build_attachment_text_block`.

        :param message: The Message object to convert.
        :return: A LangChain HumanMessage or AIMessage (never a dict).
        """
        if message.attachments:
            images = [
                ChatPromptBuilder._attachment_data_uri(attachment)
                for attachment in message.attachments
                if ChatPromptBuilder._is_image_attachment(attachment)
            ]
            if images:
                content = [
                    {"type": "text", "text": message.content}
                ] + [
                    {"type": "image_url", "image_url": {"url": src}}
                    for src in images
                ]
                if message.role == "user":
                    return HumanMessage(content=content)
                return AIMessage(content=content)
        if message.role == "user":
            return HumanMessage(content=message.content)
        return AIMessage(content=message.content)

    # ------------------------------------------------------------------
    # Multimodal content helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _get_text_content(msg):
        """
        Extract the TEXT part of a message WITHOUT touching multimodal parts.

        Messages converted from a DB message with ``attachments`` carry a LIST
        of content parts (``{"type": "text"}`` + N ``{"type": "image_url"}``).
        ``str(msg.content)`` on such a list returns its Python representation,
        which would REPLACE the images with literal text and make them
        invisible to the model — so only the text parts are joined here.

        :param msg: LangChain message object or OpenAI-style dict.
        :return: The message text (never the image parts).
        """
        if isinstance(msg, dict):
            raw = msg.get("content", "")
        else:
            raw = getattr(msg, "content", "")

        if isinstance(raw, list):
            texts = []
            for part in raw:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        texts.append(part.get("text") or "")
                elif getattr(part, "type", None) == "text":
                    texts.append(getattr(part, "text", "") or "")
            return " ".join(texts)

        return str(raw) if raw else ""

    @staticmethod
    def _set_text_content(msg, value):
        """
        Replace ONLY the TEXT part of a message, preserving multimodal parts.

        Attachment images (``{"type": "image_url"}``) are kept untouched so the
        model keeps receiving them. When the message has no text part yet, one
        is inserted at the beginning of the parts list.

        :param msg: LangChain message object or OpenAI-style dict.
        :param value: New text content.
        """
        if isinstance(msg, dict):
            existing = msg.get("content", "")
        else:
            existing = getattr(msg, "content", "")

        if isinstance(existing, list):
            for part in existing:
                if isinstance(part, dict) and part.get("type") == "text":
                    part["text"] = value
                    return
                if not isinstance(part, dict) and getattr(part, "type", None) == "text":
                    part.text = value
                    return
            existing.insert(0, {"type": "text", "text": value})
            return

        if isinstance(msg, dict):
            msg["content"] = value
        else:
            msg.content = value

    @staticmethod
    def _set_image_content_parts(msg, parts):
        """
        Replace the ``image_url`` parts of a message with *parts*.

        The message content is promoted to a parts LIST when it is still a
        plain string. Existing ``image_url`` parts are dropped first: the image
        parts are rebuilt from the collected conversation attachments
        (normalized, possibly downscaled), so keeping the ones already produced
        by :meth:`convert_message` would send the same screenshot twice — once
        with its un-normalized payload.

        :param msg: LangChain message object or OpenAI-style dict.
        :param parts: Content parts to set (``image_url`` blocks).
        """
        if isinstance(msg, dict):
            existing = msg.get("content", "")
        else:
            existing = getattr(msg, "content", "")

        if not isinstance(existing, list):
            existing = [
                {"type": "text", "text": "" if existing is None else str(existing)}
            ]
            if isinstance(msg, dict):
                msg["content"] = existing
            else:
                msg.content = existing

        existing[:] = [
            part for part in existing
            if not (isinstance(part, dict) and part.get("type") == "image_url")
        ]
        existing.extend(parts)

    @staticmethod
    def count_attachment_parts(messages):
        """
        Count the ``image_url`` parts contained in the outgoing messages.

        Used by the engine to report how many attachments are being re-sent
        with the request (traceability).

        :param messages: The assembled prompt messages.
        :return: Number of ``image_url`` content parts.
        """
        total = 0
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content")
            else:
                content = getattr(msg, "content", None)
            if isinstance(content, list):
                total += len([
                    part for part in content
                    if isinstance(part, dict) and part.get("type") == "image_url"
                ])
        return total

    # ------------------------------------------------------------------
    # Attachment payload normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _attachment_raw_data(attachment):
        """
        Return the raw (not normalized) payload stored for an attachment.

        ``ChatAttachment`` keeps the base64 payload in ``base64_data``; some
        clients store the FULL data URI there (``FileReader.readAsDataURL``).
        Legacy string attachments are JSON image descriptors
        (``{"src": "...", "alt": "..."}``).

        :param attachment: ChatAttachment or legacy string attachment.
        :return: Raw payload string (may still contain a ``data:`` prefix).
        """
        if isinstance(attachment, ChatAttachment):
            return attachment.base64_data or ""

        if isinstance(attachment, str):
            try:
                descriptor = json.loads(attachment)
            except (json.JSONDecodeError, TypeError):
                return attachment
            if isinstance(descriptor, dict):
                return str(descriptor.get("src") or "")
            return str(descriptor)

        return str(attachment)

    @classmethod
    def _attachment_media_type(cls, attachment) -> str:
        """
        Return the normalized MIME type of an attachment.

        Resolution order: stored ``file_type`` → the type declared by a stored
        data URI → the file extension. Generic values
        (``application/octet-stream``, ``image/*``) are never returned when a
        better source is available, because sending them verbatim is what makes
        a provider report "[Unsupported Image]".

        :param attachment: ChatAttachment or legacy string attachment.
        :return: Lower-case media type without parameters.
        """
        declared = ""
        file_name = ""

        if isinstance(attachment, ChatAttachment):
            declared = (attachment.file_type or "").split(";")[0].strip().lower()
            file_name = attachment.file_name or ""

        raw = cls._attachment_raw_data(attachment).strip()
        if raw.lower().startswith("data:"):
            uri_type = raw.split(";")[0].partition(":")[2].strip().lower()
            if not declared or declared in GENERIC_MEDIA_TYPES:
                declared = uri_type

        # ``image/jpg`` is not a registered type although some clients send it.
        if declared == "image/jpg":
            declared = "image/jpeg"

        if declared and declared not in GENERIC_MEDIA_TYPES:
            return declared

        extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        return MEDIA_TYPE_BY_EXTENSION.get(extension, "application/octet-stream")

    @classmethod
    def _attachment_payload(cls, attachment) -> str:
        """
        Return the bare base64 payload of an attachment.

        A stored ``data:`` URI is reduced to its payload and every whitespace
        character (JSON line wrapping, pretty printed legacy records) is
        removed: any of those makes the data URI undecodable for the provider.

        :param attachment: ChatAttachment or legacy string attachment.
        :return: Whitespace-free base64 payload (may be empty).
        """
        raw = cls._attachment_raw_data(attachment).strip()
        if raw.lower().startswith("data:"):
            raw = raw.partition(",")[2]
        return "".join(raw.split())

    @classmethod
    def _attachment_data_uri(cls, attachment) -> str:
        """
        Build the ``data:<mime>;base64,<data>`` URI used by vision APIs.

        The payload is normalized (see :meth:`_attachment_payload`) so an
        already prefixed value is never prefixed twice and no whitespace
        survives.

        :param attachment: The ChatAttachment to encode.
        :return: Data URI string.
        """
        return (
            f"data:{cls._attachment_media_type(attachment)}"
            f";base64,{cls._attachment_payload(attachment)}"
        )

    @staticmethod
    def _is_image_attachment(attachment) -> bool:
        """
        Decide whether an attachment can be sent as an ``image_url`` part.

        Only formats accepted by vision APIs qualify (PNG/JPEG/WEBP/GIF).
        Audio notes are stored the same way and must never be sent to providers
        as an image; non-raster images (SVG, BMP, TIFF, HEIC) are rejected by
        providers or replaced with an ``[Unsupported Image]`` placeholder.

        :param attachment: ChatAttachment or legacy string attachment.
        :return: True when the attachment must be sent as an image part.
        """
        return (
            ChatPromptBuilder._attachment_media_type(attachment)
            in VISION_IMAGE_MEDIA_TYPES
        )

    @staticmethod
    def _cache_store(cache: Dict[str, Any], key: str, value: Any) -> None:
        """Store *value* in *cache*, evicting the oldest entry when full."""
        if key not in cache and len(cache) >= _IMAGE_CACHE_MAX_ENTRIES:
            cache.pop(next(iter(cache)))
        cache[key] = value

    @classmethod
    def _shrink_image_data_uri(
        cls,
        data_uri: str,
        payload: str,
        file_name: str,
    ):
        """
        Downscale and re-encode an image that exceeds the inline budget.

        A screenshot bigger than the provider limit is not "seen" by the model:
        the image part is replaced with an ``[Unsupported Image]`` placeholder.
        The longest side is limited to :data:`MAX_VISION_IMAGE_SIDE` and the
        result is encoded as JPEG.

        Best effort: on any failure the original data URI is kept (and the
        reason is reported in the attachments text block). Results are cached
        per payload, since the same screenshot is re-sent on every turn.

        :param data_uri: The original (normalized) data URI.
        :param payload: The bare base64 payload of *data_uri*.
        :param file_name: Attachment name, used for logging only.
        :return: Tuple of ``(data_uri, note)`` where *note* is appended to the
                 attachment description.
        """
        cache_key = hashlib.md5(payload.encode("utf-8")).hexdigest()
        cached = _IMAGE_SHRINK_CACHE.get(cache_key)
        if cached is not None:
            return cached["uri"], cached["note"]

        result = {"uri": data_uri, "note": ""}
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(base64.b64decode(payload)))
            image.load()
            width, height = image.size

            scale = min(1.0, MAX_VISION_IMAGE_SIDE / float(max(width, height) or 1))
            if scale < 1.0:
                image = image.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.LANCZOS,
                )
            # JPEG has no alpha channel: flatten transparent screenshots.
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=True)
            candidate = (
                "data:image/jpeg;base64,"
                + base64.b64encode(buffer.getvalue()).decode("ascii")
            )

            if len(candidate) < len(data_uri):
                result = {
                    "uri": candidate,
                    "note": (
                        f", downscaled {width}x{height} -> "
                        f"{image.size[0]}x{image.size[1]} (jpeg)"
                    ),
                }
                logger.info(
                    "Attachment '%s' downscaled %sx%s -> %sx%s "
                    "(%d -> %d base64 chars)",
                    file_name, width, height,
                    image.size[0], image.size[1],
                    len(payload), len(candidate),
                )
            else:
                result = {
                    "uri": data_uri,
                    "note": ", oversized (keeping original encoding)",
                }
                logger.warning(
                    "Attachment '%s' exceeds the vision payload budget and "
                    "re-encoding did not reduce it",
                    file_name,
                )
        except ImportError:
            result = {
                "uri": data_uri,
                "note": ", oversized (Pillow not available)",
            }
            logger.warning(
                "Attachment '%s' exceeds the vision payload budget and Pillow "
                "is not installed; sending it unchanged",
                file_name,
            )
        except (binascii.Error, ValueError, KeyError, OSError, Exception) as ex:
            result = {
                "uri": data_uri,
                "note": ", oversized (re-encode failed)",
            }
            logger.warning(
                "Attachment '%s' exceeds the vision payload budget and could "
                "not be re-encoded: %s",
                file_name, ex,
            )

        cls._cache_store(_IMAGE_SHRINK_CACHE, cache_key, result)
        return result["uri"], result["note"]

    @classmethod
    def _reencode_image(cls, payload: str, file_name: str):
        """
        Decode an image with Pillow and re-encode it as PNG or JPEG.

        Covers the two cases providers cannot consume otherwise:

        * a format outside :data:`VISION_IMAGE_MEDIA_TYPES` (HEIC/HEIF from a
          phone gallery, BMP, TIFF, AVIF) is converted to a vision format,
        * an image that exceeds the inline budget is downscaled.

        Both PNG and JPEG are produced and the smaller one wins: PNG keeps
        screenshots (text) crisp, JPEG keeps photos small. Results are cached per
        payload because the same screenshot is re-sent on every turn.

        :param payload: Bare base64 payload of the image.
        :param file_name: Attachment name, used for logging only.
        :return: Tuple of ``(data_uri, note)``. *data_uri* is ``None`` when the
                 image cannot be decoded, and the note explains why.
        """
        cache_key = "reencode:" + hashlib.md5(payload.encode("utf-8")).hexdigest()
        cached = _IMAGE_SHRINK_CACHE.get(cache_key)
        if cached is not None:
            return cached["uri"], cached["note"]

        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(base64.b64decode(payload)))
            image.load()
            width, height = image.size

            scale = min(1.0, MAX_VISION_IMAGE_SIDE / float(max(width, height) or 1))
            if scale < 1.0:
                image = image.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.LANCZOS,
                )

            candidates = []

            png_buffer = io.BytesIO()
            image.save(png_buffer, format="PNG", optimize=True)
            candidates.append((
                "data:image/png;base64,"
                + base64.b64encode(png_buffer.getvalue()).decode("ascii"),
                "png",
            ))

            # JPEG has no alpha channel: flatten transparent screenshots.
            flat = image if image.mode == "RGB" else image.convert("RGB")
            jpeg_buffer = io.BytesIO()
            flat.save(jpeg_buffer, format="JPEG", quality=85, optimize=True)
            candidates.append((
                "data:image/jpeg;base64,"
                + base64.b64encode(jpeg_buffer.getvalue()).decode("ascii"),
                "jpeg",
            ))

            data_uri, output_format = min(candidates, key=lambda c: len(c[0]))
            size_note = (
                f", downscaled {width}x{height} -> "
                f"{image.size[0]}x{image.size[1]}"
                if scale < 1.0 else ""
            )
            result = {
                "uri": data_uri,
                "note": f"re-encoded as {output_format}{size_note}",
            }
            logger.info(
                "Attachment '%s' re-encoded as %s (%d -> %d base64 chars)",
                file_name, output_format, len(payload), len(data_uri),
            )
        except ImportError:
            result = {
                "uri": None,
                "note": "cannot be converted (Pillow not available)",
            }
            logger.warning(
                "Attachment '%s' is not a supported vision format and Pillow is "
                "not installed; it is NOT sent to the model",
                file_name,
            )
        except Exception as ex:  # noqa: BLE001 - best effort, never break the request
            result = {
                "uri": None,
                "note": f"cannot be converted ({type(ex).__name__})",
            }
            logger.warning(
                "Attachment '%s' could not be re-encoded for the vision API: %s",
                file_name, ex,
            )

        cls._cache_store(_IMAGE_SHRINK_CACHE, cache_key, result)
        return result["uri"], result["note"]

    @classmethod
    def _prepare_attachment(cls, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolve a single collected attachment into its outgoing representation.

        Normalizes the payload and decides whether it travels as an
        ``image_url`` content part or only as text. The returned dict is the
        single source of truth used by both
        :meth:`_build_attachment_parts` and
        :meth:`_build_attachment_text_block`.

        Every image is sent in a format the providers decode: the real format is
        detected from the payload itself (:func:`sniff_image_format`) and any
        other image (HEIC/HEIF, BMP, TIFF, AVIF, an untyped share) is re-encoded
        through :meth:`_reencode_image` instead of being dropped.

        :param item: Attachment item from :meth:`collect_attachments`.
        :return: Dict with keys ``item``, ``file_name``, ``media_type``,
                 ``size_kb``, ``part`` (image part or None) and ``note``.
        """
        attachment = item["attachment"]
        media_type = cls._attachment_media_type(attachment)
        payload = cls._attachment_payload(attachment)
        file_name = (
            attachment.file_name
            if isinstance(attachment, ChatAttachment)
            else f"legacy attachment {item.get('label', '')}".strip()
        )

        prepared = {
            "item": item,
            "file_name": file_name,
            "media_type": media_type,
            "size_kb": round(len(payload) * 3 / 4 / 1024.0, 1),
            "part": None,
            "note": "",
        }

        if not payload:
            prepared["note"] = "not sent to the model (empty payload)"
            return prepared

        # The BYTES decide, not the metadata: see :func:`sniff_image_format`.
        detected_format = sniff_image_format(payload)
        if not media_type.startswith("image/") and not detected_format:
            prepared["note"] = f"not sent to the model ({media_type} is not an image)"
            return prepared

        notes = []
        sent_media_type = MEDIA_TYPE_BY_FORMAT.get(detected_format, "")
        if sent_media_type:
            if sent_media_type != media_type:
                notes.append(f"detected {sent_media_type} instead of {media_type}")
            data_uri = f"data:{sent_media_type};base64,{payload}"
        elif media_type in VISION_IMAGE_MEDIA_TYPES:
            # Declared type is a vision format but the payload could not be
            # sniffed: keep sending it and let the provider decide.
            sent_media_type = media_type
            data_uri = f"data:{media_type};base64,{payload}"
        else:
            # HEIC/HEIF from a phone gallery, BMP, TIFF, AVIF, an untyped share:
            # decode and re-encode it instead of dropping the picture.
            data_uri, convert_note = cls._reencode_image(payload, file_name)
            if not data_uri:
                prepared["note"] = (
                    f"not sent to the model ({media_type or detected_format}, "
                    f"{convert_note})"
                )
                return prepared
            sent_media_type = data_uri.split(";")[0].partition(":")[2]
            notes.append(convert_note)

        if len(data_uri) > MAX_VISION_IMAGE_CHARS:
            data_uri, shrink_note = cls._shrink_image_data_uri(
                data_uri=data_uri,
                payload=data_uri.partition(",")[2],
                file_name=file_name,
            )
            if shrink_note:
                notes.append(shrink_note.lstrip(", "))
            sent_media_type = data_uri.split(";")[0].partition(":")[2]

        prepared["media_type"] = sent_media_type
        prepared["part"] = {"type": "image_url", "image_url": {"url": data_uri}}
        details = "".join(f", {note}" for note in notes if note)
        prepared["note"] = (
            f"sent to the model as image ({sent_media_type}{details})"
        )
        return prepared

    # ------------------------------------------------------------------
    # Attachment visibility
    # ------------------------------------------------------------------
    def collect_attachments(self, chat, include_parent=True):
        """
        Collect every attachment the model must always see.

        Attachments of ALL visible messages are returned — parent chats first
        (grandparents first), then the current chat, in conversation order — so
        an image uploaded in an earlier turn keeps reaching the model on every
        following request instead of being lost with the text-only history
        rendering.

        Visibility rules:

        * current chat: ``hide`` / ``improvement`` messages are skipped,
        * parent chats: every VISIBLE message contributes (the ``is_answer``
          flag is not required — see
          :meth:`is_visible_parent_message`), and the traversal stops on
          ``ignore_parent_knowledge``,
        * duplicated payloads (same base64 data) are collapsed to one item.

        :param chat: The chat whose messages are inspected.
        :param include_parent: Whether parent chats contribute attachments.
        :return: List of ``{"role", "label", "attachment"}`` dicts in order.
        """
        items = []
        seen_payloads = set()

        if include_parent and chat.parent_id:
            self._collect_parent_attachments(
                chat=chat,
                items=items,
                seen_payloads=seen_payloads,
            )

        for index, message in enumerate(chat.messages):
            if message.hide or message.improvement or message.unsent:
                continue
            self._add_message_attachments(
                message=message,
                items=items,
                seen_payloads=seen_payloads,
                source="chat",
                index=index,
            )

        return items

    def _collect_parent_attachments(self, chat, items, seen_payloads):
        """
        Append the attachments of a chat's ancestor chain (grandparents first).

        Mirrors :meth:`build_parent_message_history`: stops on
        ``ignore_parent_knowledge`` and includes only VISIBLE messages (via
        :meth:`is_visible_parent_message`, i.e. no ``is_answer`` filtering), so
        attachments keep the same context as the rendered parent history.

        :param chat: The child chat whose ancestors are visited.
        :param items: Accumulator list of attachment items.
        :param seen_payloads: Set of already collected base64 payloads.
        """
        if not chat.parent_id or chat.ignore_parent_knowledge:
            return

        chat_manager = self._get_chat_manager(project_id=chat.owner_project_id)
        parent_chat = chat_manager.find_by_id(chat.parent_id)
        if not parent_chat:
            return

        self._collect_parent_attachments(
            chat=parent_chat,
            items=items,
            seen_payloads=seen_payloads,
        )

        for index, message in enumerate(parent_chat.messages):
            if not self.is_visible_parent_message(message):
                continue
            self._add_message_attachments(
                message=message,
                items=items,
                seen_payloads=seen_payloads,
                source="parent",
                index=index,
            )

    @staticmethod
    def _add_message_attachments(message, items, seen_payloads, source, index):
        """
        Append a single message's attachments to the collector.

        :param message: The message whose attachments are collected.
        :param items: Accumulator list of attachment items.
        :param seen_payloads: Set of already collected base64 payloads.
        :param source: Label prefix ("chat" or "parent").
        :param index: Message position inside its chat (used in the label).
        """
        role = message.role or "user"
        for attachment in (message.attachments or []):
            payload = (
                attachment.base64_data
                if isinstance(attachment, ChatAttachment)
                else str(attachment)
            )
            if payload in seen_payloads:
                continue
            seen_payloads.add(payload)
            items.append({
                "role": role,
                "label": f"{source} turn {index + 1} · {role}",
                "attachment": attachment,
            })

    @staticmethod
    def _build_attachment_parts(prepared_items):
        """
        Collect the ``image_url`` content parts of the prepared attachments.

        Attachments that cannot be sent as images (audio, unsupported raster
        formats, empty payloads) only appear in the text block.

        :param prepared_items: Output of :meth:`_prepare_attachment`.
        :return: List of ``{"type": "image_url"}`` content parts.
        """
        return [
            prepared["part"]
            for prepared in prepared_items
            if prepared.get("part")
        ]

    @staticmethod
    def _build_attachment_text_block(prepared_items):
        """
        Render the attachment list included in the request text.

        Gives the model a textual trace of every attachment (origin turn, file
        name, media type, size) and — crucially — states whether each one was
        sent as an image or not, so a model that cannot decode an image part can
        still report exactly what it received.

        :param prepared_items: Output of :meth:`_prepare_attachment`.
        :return: Markdown block, or an empty string when there is nothing.
        """
        if not prepared_items:
            return ""

        lines = ["## Attachments"]
        for prepared in prepared_items:
            lines.append(
                f"- [{prepared['item']['label']}] {prepared['file_name']} "
                f"({prepared['media_type']}, {prepared['size_kb']} KB) — "
                f"{prepared['note']}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # History rendering
    # ------------------------------------------------------------------
    @staticmethod
    def _render_history_message(message, formatted_content=None):
        """
        Render a single prior message as an inert turn block.

        Empty messages are skipped (empty string): a run that only executed
        tools produces an assistant message with no text, and its work is
        carried by the ``<session_memory>`` block instead of an empty turn.
        """
        content = (
            formatted_content if formatted_content is not None
            else (message.content or "")
        )
        if not content.strip():
            return ""
        tag = "user" if message.role == "user" else "assistant_done"
        return f"<{tag}>\n{content}\n</{tag}>"

    @staticmethod
    def is_visible_parent_message(message):
        """
        Decide whether a parent chat message should be loaded as content.

        CHANGED: parent content is no longer restricted to messages flagged as
        ``is_answer``. Every VISIBLE message (``hide == False``) of the ancestor
        chats is included, matching what the user sees in the parent chat.

        :param message: The parent chat message to evaluate.
        :return: True when the message must be part of the parent context.
        """
        return not message.hide and not message.unsent

    @staticmethod
    def format_parent_message(message):
        """Render a parent message using content, files and attachments."""
        parts = []

        if message.content:
            parts.append(message.content)

        if message.files:
            files_block = "\n".join(f"- {file_path}" for file_path in message.files)
            parts.append(f"Files:\n{files_block}")

        if message.attachments:
            attachments_block = "\n".join(
                f"- {attachment.file_name} ({attachment.file_type})"
                for attachment in message.attachments
            )
            parts.append(f"Attachments:\n{attachments_block}")

        return concat_content(parts)

    def build_parent_message_history(self, chat):
        """
        Build rendered strings from a chat's ancestor chain.

        Only VISIBLE (not hidden) messages are included — the ``is_answer``
        flag is no longer required (see :meth:`is_visible_parent_message`).
        Respects ``ignore_parent_knowledge`` during traversal.

        :return: List of rendered turn strings (grandparents first).
        """
        parts = []

        if not chat.parent_id or chat.ignore_parent_knowledge:
            return parts

        chat_manager = self._get_chat_manager(project_id=chat.owner_project_id)
        parent_chat = chat_manager.find_by_id(chat.parent_id)

        if not parent_chat:
            logger.warning(
                "Parent chat '%s' not found for chat '%s' "
                "(owner_project_id='%s')",
                chat.parent_id,
                chat.doc_id,
                chat.owner_project_id,
            )
            return parts

        logger.info(
            "Including parent chat messages for chat '%s' "
            "(parent_id='%s', ignore_parent_knowledge=%s)",
            chat.doc_id,
            chat.parent_id,
            chat.ignore_parent_knowledge,
        )

        parts.extend(self.build_parent_message_history(chat=parent_chat))

        for message in parent_chat.messages:
            if not self.is_visible_parent_message(message):
                continue

            formatted_content = self.format_parent_message(message)
            if not formatted_content:
                continue

            parts.append(
                self._render_history_message(
                    message, formatted_content=formatted_content
                )
            )

        return parts

    def collect_prior_messages(
        self,
        chat,
        include_parent=True,
        only_answers=False,
    ):
        """
        Return the prior VISIBLE messages of a chat, ancestors first.

        Used to build the tool memory of the next turn: it must see the tool
        events attached to every completed message, including the ones of the
        ancestor chats that contribute context (unless
        ``ignore_parent_knowledge`` is set).

        The last message of the current chat is EXCLUDED: it is the live user
        request and has no prior tool events.

        :param chat: The chat whose prior messages are collected.
        :param include_parent: Whether ancestor chats contribute messages.
        :param only_answers: When True, restrict CURRENT-chat messages to
                             ``is_answer`` ones (refine/vibe mode). Parent
                             messages are not filtered, mirroring
                             :meth:`build_message_history`.
        :return: List of visible Message objects in chronological order.
        """
        messages = []

        if include_parent and chat.parent_id and not chat.ignore_parent_knowledge:
            self._collect_parent_messages(chat=chat, messages=messages)

        for message in chat.messages[0:-1]:
            if message.hide or message.improvement or message.unsent:
                continue
            if only_answers and not message.is_answer:
                continue
            messages.append(message)

        return messages

    def _collect_parent_messages(self, chat, messages):
        """
        Append the visible messages of a chat's ancestor chain (oldest first).

        Mirrors :meth:`build_parent_message_history`: stops on
        ``ignore_parent_knowledge`` and includes only visible messages.

        :param chat: The child chat whose ancestors are visited.
        :param messages: Accumulator list (mutated in place).
        """
        if not chat.parent_id or chat.ignore_parent_knowledge:
            return

        chat_manager = self._get_chat_manager(project_id=chat.owner_project_id)
        parent_chat = chat_manager.find_by_id(chat.parent_id)
        if not parent_chat:
            logger.warning(
                "Parent chat '%s' not found while collecting tool memory of "
                "chat '%s'",
                chat.parent_id,
                chat.doc_id,
            )
            return

        self._collect_parent_messages(chat=parent_chat, messages=messages)

        for message in parent_chat.messages:
            if not self.is_visible_parent_message(message):
                continue
            messages.append(message)

    def build_message_history(
        self,
        chat,
        include_parent_messages=True,
        only_answers=False,
        tool_memory_section=None,
    ):
        """
        Build a single inert history message from prior turns.

        All prior turns (parent + current, excluding the last/current user
        message) are collapsed into one ``<conversation_history>`` block so the
        model sees them as completed context rather than live instructions.

        ADDED: the block also carries the tool memory of the previous turns
        (``<session_memory>``) built from the tool events already persisted on
        the chat messages. This is what stops the agent from re-running the
        same reads/searches on every turn. Callers may pass a pre-built
        *tool_memory_section* (the engine does, to log its stats); when None it
        is built here from the prior messages.

        NOTE: the history block is TEXT only. Attachments of prior turns are
        not lost: they are hoisted into the current request message by
        :meth:`build_ai_prompt_messages` (see :meth:`collect_attachments`).

        :param chat: The chat whose history to convert.
        :param include_parent_messages: Whether to include parent chat history.
        :param only_answers: When True, restrict CURRENT-chat history to
                             ``is_answer`` messages (used in refine/vibe mode).
                             Parent chat history is never filtered by
                             ``is_answer``.
        :param tool_memory_section: Optional pre-rendered ``<session_memory>``
                                    block (built by the caller).
        :return: List containing one HumanMessage, or [] when empty.
        """
        parts = []

        if include_parent_messages and chat.parent_id and not chat.ignore_parent_knowledge:
            parts.extend(self.build_parent_message_history(chat=chat))
        elif chat.parent_id and chat.ignore_parent_knowledge:
            logger.info(
                "Skipping parent chat messages for chat '%s' "
                "(ignore_parent_knowledge=True)",
                chat.doc_id,
            )

        for message in chat.messages[0:-1]:
            if message.hide or message.improvement or message.unsent:
                continue
            if only_answers and not message.is_answer:
                continue
            rendered = self._render_history_message(message)
            if rendered:
                parts.append(rendered)

        if tool_memory_section is None:
            tool_memory_section, _ = build_tool_memory_section(
                self.collect_prior_messages(
                    chat=chat,
                    include_parent=include_parent_messages,
                    # Tool memory is never filtered by ``is_answer``: it
                    # describes work already done, not rendered answers.
                    only_answers=False,
                )
            )

        if not parts and not tool_memory_section:
            return []

        blocks = []
        if parts:
            blocks.append(
                CONVERSATION_HISTORY_HEADER
                + "\n".join(parts)
                + "\n"
                + CONVERSATION_HISTORY_FOOTER
            )
        if tool_memory_section:
            blocks.append(tool_memory_section)

        return [HumanMessage(content="\n".join(blocks))]

    # ------------------------------------------------------------------
    # Final prompt assembly
    # ------------------------------------------------------------------
    def build_ai_prompt_messages(
        self,
        chat,
        messages,
        user_message,
        last_ai_message,
        context,
        chat_files_content,
        is_refine,
    ):
        """
        Assemble the final list of LangChain messages to send to the AI.

        Appends context, working-file content, and the mode-specific prompt.
        The live instruction is wrapped in ``<current_request>`` and kept as the
        LAST message so it is clearly separated from inert history.

        CHANGED: the ``<current_request>`` wrapping and the working-files
        preamble only rewrite the TEXT part of the last message
        (:meth:`_get_text_content` / :meth:`_set_text_content`). Message
        attachments (vision ``image_url`` parts) are therefore preserved and
        remain visible to the model instead of being flattened into a string.

        ADDED: attachments of EVERY visible message (current chat and parent
        chats) are appended to the request message, so ``message.attachments``
        keeps reaching the model on every following turn. Images are sent as
        ``image_url`` parts; any other type is listed in the ``## Attachments``
        text block only.

        CHANGED: the image parts are REPLACED by the normalized ones built from
        :meth:`_prepare_attachment` (payload without a duplicated ``data:``
        prefix, whitespace stripped, media type resolved, oversized screenshots
        downscaled), so each attachment is sent exactly once and in a form the
        provider can actually decode.

        :return: Mutated messages list ready for AI invocation.
        """
        if context:
            messages.append(
                self.convert_message(
                    new_chat_message(
                        role="user",
                        content=f"<project_files>{context}</project_files>",
                    )
                )
            )

        if is_refine:
            messages = self.append_refine_message(
                chat=chat,
                messages=messages,
                user_message=user_message,
                last_ai_message=last_ai_message,
            )
        else:
            messages.append(self.convert_message(user_message))

        # Wrap the live request so the model can distinguish it from history.
        # Only the text part is rewritten: attachment images must survive.
        last_msg = messages[-1]
        current_content = self._get_text_content(last_msg)
        self._set_text_content(
            last_msg,
            f"<current_request>\n{current_content}\n</current_request>",
        )

        if chat_files_content:
            last_msg = messages[-1]
            current_content = self._get_text_content(last_msg)
            new_content = (
                f"## Working Files:\n"
                f"{chat_files_content}\n"
                f"\n## User request\n"
                f"{current_content}"
            )
            self._set_text_content(last_msg, new_content)

        # ADDED: make every conversation attachment visible in the single
        # request message (images as content parts, everything else as text).
        attachment_items = self.collect_attachments(
            chat=chat,
            include_parent=not chat.ignore_parent_knowledge,
        )
        if attachment_items:
            prepared_items = [
                self._prepare_attachment(item) for item in attachment_items
            ]
            last_msg = messages[-1]
            current_content = self._get_text_content(last_msg)
            self._set_text_content(
                last_msg,
                f"{self._build_attachment_text_block(prepared_items)}"
                f"\n\n{current_content}",
            )
            self._set_image_content_parts(
                last_msg,
                self._build_attachment_parts(prepared_items),
            )
            logger.info(
                "Attached %d conversation attachment(s) to the request of "
                "chat '%s': %s",
                len(prepared_items),
                chat.doc_id,
                "; ".join(
                    f"{prepared['file_name']} ({prepared['media_type']}, "
                    f"{prepared['size_kb']} KB) -> {prepared['note']}"
                    for prepared in prepared_items
                ),
            )

        self._redact_for_model(messages, chat=chat)

        return messages

    def _redact_for_model(self, messages, chat=None):
        """Neutralise secrets in every outgoing message (CISO).

        A prompt is a permanent third-party disclosure: the provider stores it,
        and the same text is echoed back into the answer and from there into the
        task card. Tool output is the usual carrier (a ``cat`` of a config file,
        a ``git remote -v``, a container log), which is why the check runs here
        instead of trusting every caller.

        Only the TEXT part is rewritten: attachment images
        (``{"type": "image_url"}``) are preserved, see
        :meth:`_get_text_content` / :meth:`_set_text_content`.

        :param messages: The messages about to be sent to the model.
        :param chat: The chat being processed (used in the log line).
        :return: Number of redacted occurrences.
        """
        replacements = 0
        for message in messages:
            try:
                text = self._get_text_content(message)
            except Exception:  # pragma: no cover - defensive
                continue
            if not text:
                continue
            redacted, found = redact_text(text)
            if not found:
                continue
            replacements += found
            self._set_text_content(message, redacted)
        if replacements:
            logger.warning(
                "CISO: redacted %d secret(s) from the prompt of chat '%s' "
                "before sending it to the model",
                replacements,
                getattr(chat, "doc_id", None) or getattr(chat, "id", None),
            )
        return replacements

    def append_refine_message(self, chat, messages, user_message, last_ai_message):
        """
        Append a task-refinement prompt to the message list.

        Attachments are no longer copied here: they are collected from every
        visible message (including the current one) and appended to the request
        message by :meth:`build_ai_prompt_messages`.
        """
        existing_document = last_ai_message.content if last_ai_message else ""
        parent_task = ""

        if not chat.ignore_parent_knowledge:
            parent_task = self._get_chat_analysis_parents(chat=chat)

        task_content = ""

        answer_messages = [
            message.content for message in chat.messages if message.is_answer
        ]
        if answer_messages:
            task_content += "Task Document Header:\n"
            task_content += concat_content(answer_messages)
            task_content += "\n\n"

        if parent_task:
            task_content += (
                f"\nYou are writing a child document.\n"
                f"This information comes from the parent document for your information:\n"
                f"<parent_document>\n{parent_task}\n</parent_document>\n"
            )

        if existing_document:
            task_content += (
                f"\n<document>\n{existing_document}\n</document>\n\n"
                f"<comments>\n{user_message.content}\n</comments>\n\n"
                f"INSTRUCTIONS:\n"
                f" * Read comments and update document content based on them\n"
                f" * Leave all parts of the document not affected by the comments untouched\n"
                f" * Output the final document content\n"
            )
        else:
            task_content += user_message.content

        refine_message = new_chat_message(role="user", content=task_content)
        messages.append(self.convert_message(refine_message))
        return messages

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------
    @staticmethod
    def extract_files_from_response(content):
        """
        Extract code blocks with filenames from the AI response content.
        """
        files = []
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]
            open_match = re.match(r'^```(\w+)\s+(.+)$', line)

            if open_match:
                language = open_match.group(1)
                file_path = open_match.group(2).strip()
                code_lines = []
                i += 1
                nesting_depth = 1

                while i < len(lines) and nesting_depth > 0:
                    current_line = lines[i]

                    if re.match(r'^```(\w+)', current_line):
                        nesting_depth += 1
                        code_lines.append(current_line)
                    elif current_line == '```':
                        nesting_depth -= 1
                        if nesting_depth > 0:
                            code_lines.append(current_line)
                    else:
                        code_lines.append(current_line)

                    i += 1

                if file_path:
                    files.append({
                        "language": language,
                        "file_path": file_path,
                        "content": "\n".join(code_lines),
                    })
            else:
                i += 1

        logger.info("Extracted %d files from response content", len(files))
        return files