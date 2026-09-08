"""HuggingFace cache helpers for safe, writable runtime paths."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Iterable

# Silence HuggingFace and tokenizer noise — only ERROR+ logs appear
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

_DEFAULT_HF_HOME = "/tmp/huggingface"
_FALLBACK_HF_HOME = "/tmp/huggingface_fallback"


def _is_permission_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 13:
        return True
    msg = str(exc).lower()
    return "permission denied" in msg or "errno 13" in msg


def _build_cache_paths(base_dir: str) -> Dict[str, str]:
    base = base_dir.rstrip("/") or _DEFAULT_HF_HOME
    return {
        "HF_HOME": base,
        "HF_HUB_CACHE": os.path.join(base, "hub"),
        "SENTENCE_TRANSFORMERS_HOME": os.path.join(base, "st"),
    }


def _ensure_dirs(paths: Iterable[str], logger: logging.Logger | None = None) -> None:
    for path in paths:
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            if logger:
                logger.warning("HF cache dir create failed (%s): %s", path, exc)


def configure_hf_cache(
    base_dir: str | None = None,
    logger: logging.Logger | None = None,
    override: bool = False,
) -> Dict[str, str]:
    base = base_dir or os.environ.get("HF_HOME") or _DEFAULT_HF_HOME
    paths = _build_cache_paths(base)
    for key, value in paths.items():
        if override or key not in os.environ:
            os.environ[key] = value
    _ensure_dirs(paths.values(), logger=logger)
    return paths


def init_sentence_transformer(
    model_name: str,
    logger: logging.Logger | None = None,
) -> "SentenceTransformer":
    """Initialize SentenceTransformer with cache safety and a fallback dir."""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # pragma: no cover - missing dependency
        raise RuntimeError("sentence-transformers is not available") from exc

    configure_hf_cache(logger=logger)
    try:
        return SentenceTransformer(model_name)
    except Exception as exc:
        if _is_permission_error(exc):
            if logger:
                logger.warning(
                    "SentenceTransformer init failed due to cache permissions; "
                    "retrying with fallback cache dir."
                )
            fallback = os.environ.get("HF_HOME_FALLBACK", _FALLBACK_HF_HOME)
            configure_hf_cache(base_dir=fallback, logger=logger, override=True)
            try:
                return SentenceTransformer(model_name)
            except Exception as exc2:
                if logger:
                    logger.error(
                        "SentenceTransformer init failed after fallback cache dir: %s",
                        exc2,
                    )
                raise RuntimeError(
                    "SentenceTransformer init failed after fallback cache directory"
                ) from exc2
        if logger:
            logger.error("SentenceTransformer init failed: %s", exc)
        raise RuntimeError(f"SentenceTransformer init failed: {exc}") from exc


# ── Process-wide shared embedder cache ───────────────────────────────────────
# Keyed by model_name; populated lazily on first call for each model.
_model_cache: Dict[str, "SentenceTransformer"] = {}


def get_shared_embedder(
    model_name: str,
    logger: logging.Logger | None = None,
) -> "SentenceTransformer":
    """Return a process-wide cached SentenceTransformer; loads once per model_name."""
    if model_name not in _model_cache:
        _model_cache[model_name] = init_sentence_transformer(model_name, logger=logger)
    return _model_cache[model_name]
