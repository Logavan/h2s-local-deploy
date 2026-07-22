"""Opt-in node-dictionary checkpoints for focused conversion debugging.

Checkpoint files contain customer XML and generated SQL. They are intended only for
trusted local debugging and must never be committed or loaded from an untrusted source.
"""

from __future__ import annotations

import logging
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

CAPTURE_ENV_VAR = "H2S_CAPTURE_SOURCE_DATATYPE_CHECKPOINTS"
CHECKPOINT_DIR_ENV_VAR = "H2S_DEBUG_CHECKPOINT_DIR"
DEFAULT_CHECKPOINT_DIR = Path(__file__).resolve().parent / "debug_checkpoints"

_STAGE_FILENAMES = {
    "before": "source_datatype_before.pkl",
    "after": "source_datatype_after.pkl",
    "after_fill_replay": "source_datatype_after_fill_replay.pkl",
}


def checkpoint_capture_enabled() -> bool:
    """Return whether automatic source-datatype checkpoint capture is enabled."""
    return os.getenv(CAPTURE_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_checkpoint_dir(directory: Optional[Path] = None) -> Path:
    """Resolve the configured checkpoint directory."""
    if directory is not None:
        return Path(directory).resolve()

    configured_directory = os.getenv(CHECKPOINT_DIR_ENV_VAR)
    if configured_directory:
        return Path(configured_directory).expanduser().resolve()

    return DEFAULT_CHECKPOINT_DIR


def get_checkpoint_path(stage: str, directory: Optional[Path] = None) -> Path:
    """Return the fixed checkpoint path for a supported stage."""
    try:
        filename = _STAGE_FILENAMES[stage]
    except KeyError as exc:
        supported = ", ".join(sorted(_STAGE_FILENAMES))
        raise ValueError(
            f"Unsupported checkpoint stage '{stage}'. Expected one of: {supported}."
        ) from exc

    return resolve_checkpoint_dir(directory) / filename


def save_node_dict_checkpoint(
    node_dict: Mapping[str, Any],
    stage: str,
    directory: Optional[Path] = None,
) -> Path:
    """Atomically save a trusted local ``node_dict`` checkpoint."""
    checkpoint_path = get_checkpoint_path(stage, directory)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{checkpoint_path.stem}.",
            suffix=".tmp",
            dir=checkpoint_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            pickle.dump(dict(node_dict), temporary_file, protocol=pickle.HIGHEST_PROTOCOL)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, checkpoint_path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

    logger.info(
        "Saved %s source-datatype checkpoint with %d nodes to %s.",
        stage,
        len(node_dict),
        checkpoint_path,
    )
    return checkpoint_path


def capture_node_dict_checkpoint(
    node_dict: Mapping[str, Any], stage: str
) -> Optional[Path]:
    """Save an automatic checkpoint when the opt-in environment flag is set."""
    if not checkpoint_capture_enabled():
        return None

    try:
        return save_node_dict_checkpoint(node_dict, stage)
    except (OSError, pickle.PickleError, TypeError, ValueError):
        # Debug capture must never fail a paid production conversion.
        logger.exception("Unable to save the %s node-dictionary checkpoint.", stage)
        return None


def load_node_dict_checkpoint(
    stage: str, directory: Optional[Path] = None
) -> dict[str, Any]:
    """Load a trusted local checkpoint created by this application."""
    checkpoint_path = get_checkpoint_path(stage, directory)
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

    with checkpoint_path.open("rb") as checkpoint_file:
        loaded_value = pickle.load(checkpoint_file)

    if not isinstance(loaded_value, dict):
        raise TypeError(
            f"Checkpoint {checkpoint_path} contains {type(loaded_value).__name__}, "
            "not a node dictionary."
        )

    return loaded_value
