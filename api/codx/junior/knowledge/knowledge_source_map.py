"""
Source map: the small JSON file that describes what is indexed for a project.

Every knowledge backend (``knowledge_db`` / Milvus, ``knowledge_lancedb`` /
LanceDB) keeps a *source map* next to its database:

    <codx_path>/db/<index_name>/<index_name>_file.json

The source map is the single source of truth for ``get_all_sources`` and
``get_all_categoties``, so listing indexed files never has to scan the vector
database. It is bootstrapped from the database the first time it is missing and
kept in sync on every index / delete operation.

Structure::

    {
        "version":    int,          # schema version for forward compat
        "index_name": str,          # collection/table name this map belongs to
        "updated_at": int,          # unix timestamp of last write
        "sources": {
            "<source_path>": {
                "last_update": int,     # unix timestamp of last index op
                "category":    str,
                "keywords":    list[str]
            },
            ...
        },
        "categories": list[str]     # pre-aggregated, deduplicated
    }

The module is backend agnostic on purpose: the file format is shared, so
switching ``CODX_JUNIOR_KNOWLEDGE_BACKEND`` never invalidates it.

Made with ❤️ by codx-junior
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Static file schema version — increment when the structure changes
SOURCE_MAP_VERSION = 1


def build_empty_source_map(index_name: str) -> Dict[str, Any]:
    """
    Return a fresh, empty source-map skeleton.

    Args:
        index_name: The collection (Milvus) or table (LanceDB) name.

    Returns:
        Empty source-map dict.
    """
    return {
        "version": SOURCE_MAP_VERSION,
        "index_name": index_name,
        "updated_at": int(time.time()),
        "sources": {},
        "categories": [],
    }


def read_source_map(path: str) -> Optional[Dict[str, Any]]:
    """
    Load and return the source-map JSON from *path*.

    Args:
        path: Absolute path to the source-map JSON file.

    Returns:
        Parsed source-map dict, or None when the file does not exist or cannot
        be parsed.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as ex:
        logger.warning("Could not read source-map '%s': %s", path, ex)
        return None


def write_source_map(path: str, source_map: Dict[str, Any]) -> None:
    """
    Persist *source_map* as formatted JSON to *path* atomically.

    Uses a temporary sibling file + ``os.replace`` to avoid partial writes.

    Args:
        path:       Absolute path for the destination file.
        source_map: Source-map dict to serialise.
    """
    source_map["updated_at"] = int(time.time())
    tmp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(source_map, fh, indent=2)
        os.replace(tmp_path, path)
        logger.debug("Source-map written to '%s'", path)
    except Exception as ex:
        logger.error("Could not write source-map '%s': %s", path, ex)
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def rebuild_categories(source_map: Dict[str, Any]) -> None:
    """
    Re-derive the ``categories`` list from the current ``sources`` entries.

    Args:
        source_map: Source-map dict updated in place.
    """
    cats: Set[str] = set()
    for entry in source_map["sources"].values():
        cat = entry.get("category", "")
        if cat:
            cats.add(cat)
    source_map["categories"] = sorted(cats)


def parse_keywords(raw_keywords: Any) -> List[str]:
    """
    Normalise a keywords value to a list of non-empty strings.

    Documents carry keywords either as a list or as a comma separated CSV
    string (that is how they are stored in the database column).

    Args:
        raw_keywords: List, CSV string or None.

    Returns:
        List of stripped keywords.
    """
    if not raw_keywords:
        return []
    if isinstance(raw_keywords, str):
        return [k.strip() for k in raw_keywords.split(",") if k.strip()]
    return [str(k).strip() for k in raw_keywords if str(k).strip()]

# Made with ❤️ by codx-junior
