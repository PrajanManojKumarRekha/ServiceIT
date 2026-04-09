import logging
import os
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import matplotlib

os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


def generate_error_timeline_chart(
    timestamps: Sequence[datetime],
    error_counts: Sequence[int],
    output_path: str | None = None,
) -> str:
    if len(timestamps) != len(error_counts):
        raise ValueError("timestamps and error_counts must have the same length")
    if not timestamps:
        raise ValueError("At least one timestamp is required to generate a chart")

    destination = (
        Path(output_path)
        if output_path
        else Path(tempfile.gettempdir()) / "serviceit_error_timeline.png"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    try:
        ax.plot(timestamps, error_counts, color="#b42318", linewidth=2.0, marker="o")
        ax.fill_between(timestamps, error_counts, color="#fecdca", alpha=0.6)
        ax.set_title("Error Timeline", fontsize=14)
        ax.set_xlabel("Timestamp", fontsize=11)
        ax.set_ylabel("Errors", fontsize=11)
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.4)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(destination, dpi=150, bbox_inches="tight")
    finally:
        plt.close(fig)

    logger.info("Chart written: %s", destination)
    return os.fspath(destination)
