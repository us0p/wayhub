"""Turning a submission into the posting's text (M7, D9, D78): a PDF's text layer (or the model
reading the scanned pages), screenshots read by the model in one call, or a web page (`fetch`).
"""

import base64
import io
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.bundle import AI
from mentor.ai.ports import Effort
from mentor.jobs import fetch, prompts
from mentor.jobs.errors import NeedsManualError, NotAJobError
from mentor.jobs.models import JobPosting, JobSource, JobUpload

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 10
# A PDF whose text layer is shorter than this is treated as scanned (images of text).
MIN_PDF_TEXT_CHARS = 200


async def read(db: AsyncSession, ai: AI, job: JobPosting) -> str:
    match job.source:
        case JobSource.URL:
            assert job.source_url is not None
            return await fetch.page_text(job.source_url)
        case JobSource.PDF:
            [upload] = await _uploads(db, job)
            return await _pdf_text(ai, upload)
        case JobSource.IMAGES:
            uploads = await _uploads(db, job)
            return await _transcribe(ai, [_image_block(u) for u in uploads])
        case JobSource.TEXT:
            raise ValueError("pasted postings already have their text")


async def _uploads(db: AsyncSession, job: JobPosting) -> list[JobUpload]:
    uploads = list(
        await db.scalars(
            select(JobUpload).where(JobUpload.job_id == job.id).order_by(JobUpload.position)
        )
    )
    if not uploads:
        raise ValueError("the posting's files are gone")
    return uploads


async def _pdf_text(ai: AI, upload: JobUpload) -> str:
    try:
        reader = PdfReader(io.BytesIO(upload.data))
        encrypted = reader.is_encrypted
        pages = [] if encrypted else reader.pages[:MAX_PDF_PAGES]
        text = "\n".join((page.extract_text() or "") for page in pages).strip()
    except Exception:  # pypdf raises many kinds of errors on malformed files
        raise NeedsManualError("pdf_unreadable") from None
    if encrypted:
        raise NeedsManualError("pdf_unreadable")
    if len(text) >= MIN_PDF_TEXT_CHARS:
        return text
    # Scanned: no text layer, so the model reads the pages.
    block = {"type": "file", "base64": _b64(upload.data), "mime_type": upload.content_type}
    return await _transcribe(ai, [block])


def _image_block(upload: JobUpload) -> dict[str, Any]:
    return {"type": "image", "base64": _b64(upload.data), "mime_type": upload.content_type}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def _transcribe(ai: AI, blocks: list[dict[str, Any]]) -> str:
    """One model call for every image (or the scanned PDF) of the posting."""
    content: list[Any] = [{"type": "text", "text": "Transcreva o anúncio de vaga."}, *blocks]
    messages = [SystemMessage(prompts.TRANSCRIBE_SYSTEM), HumanMessage(content=content)]
    reply = await ai.chat.chat(effort=Effort.LOW).ainvoke(messages)
    text = reply.text.strip()
    if not text or text.upper().startswith(prompts.NO_JOB_MARKER):
        raise NotAJobError
    return text
