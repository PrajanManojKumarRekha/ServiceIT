import logging
import os
from functools import partial

from dotenv import load_dotenv
from langchain_core.runnables.config import run_in_executor
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession

load_dotenv()

logger = logging.getLogger(__name__)

_EMBEDDING_DIMENSION = 768
_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "models/text-embedding-004")
_SUPPORTED_EMBEDDING_MODEL = "models/gemini-embedding-001"
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not _GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is required for embedding service")

_embeddings = GoogleGenerativeAIEmbeddings(
    model=(
        _SUPPORTED_EMBEDDING_MODEL
        if _EMBEDDING_MODEL == "models/text-embedding-004"
        else _EMBEDDING_MODEL
    ),
    google_api_key=_GEMINI_API_KEY,
)


async def embed_text(text: str) -> list[float]:
    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValueError("Text to embed must not be empty")

    vector = await run_in_executor(
        None,
        partial(
            _embeddings.embed_query,
            cleaned_text,
            output_dimensionality=_EMBEDDING_DIMENSION,
        ),
    )
    if len(vector) != _EMBEDDING_DIMENSION:
        raise ValueError(
            f"Unexpected embedding dimension {len(vector)}; expected {_EMBEDDING_DIMENSION}"
        )

    return [float(value) for value in vector]


async def reembed_incident(
    incident_id: int,
    corrected_text: str,
    session: AsyncSession,
) -> None:
    """Re-embed corrected root cause and overwrite both the Incident and its linked Trace embedding."""
    from sqlalchemy import select
    from backend.models import Incident, Trace

    result = await session.execute(
        select(Incident).where(Incident.id == incident_id)
    )
    incident = result.scalar_one_or_none()
    if incident is None:
        raise ValueError(f"Incident {incident_id} not found")

    new_vector = await embed_text(corrected_text)

    trace_result = await session.execute(
        select(Trace).where(Trace.trace_id == incident.trace_id)
    )
    trace = trace_result.scalar_one_or_none()
    if trace is not None:
        trace.embedding = new_vector

    logger.info("Re-embedded incident %d with corrected root cause", incident_id)
