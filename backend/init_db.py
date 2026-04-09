import asyncio
import logging

from dotenv import load_dotenv
from sqlalchemy import text

from backend.database import Base, engine

load_dotenv()

logging.basicConfig(
    level="INFO",
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def init_db() -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables created. pgvector extension enabled.")
    except Exception:
        logger.exception("Failed to initialize database")
        raise


if __name__ == "__main__":
    asyncio.run(init_db())
