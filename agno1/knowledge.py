"""Knowledge base setup and configuration for AgentOS."""

__all__ = [
    "list_knowledge_bases",
    "create_knowledge_base",
    "prepare_knowledge_base",
    "list_kb_entries",
]

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agno.knowledge.chunking.markdown import MarkdownChunking
from agno.knowledge.document import Document
from agno.knowledge.knowledge import Knowledge
from agno.knowledge.reader.markdown_reader import MarkdownReader
from agno.utils.log import log_debug, log_error, log_info, log_warning

from .nano_vecdb import NanoVecDb
from .utils import get_project_root


async def list_knowledge_bases() -> List[str]:
    """List all available knowledge bases (subdirectories in the knowledge folder).

    Returns:
        List of knowledge base names (subdirectory names)
    """
    subdirs: List[str] = []
    try:
        project_root = get_project_root()
        knowledge_path = Path(project_root) / "knowledge"
        log_debug(f"🔍 Scanning knowledge directory: {knowledge_path}")

        if knowledge_path.exists():
            for item in knowledge_path.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    subdirs.append(item.name)
            log_debug(f"📚 Found {len(subdirs)} knowledge base directories: {subdirs}")
        else:
            log_warning(f"⚠️ Knowledge directory does not exist: {knowledge_path}")
    except Exception:
        log_error("Error getting knowledge bases", exc_info=True)
    return subdirs


async def list_kb_entries(
    kb_name: str, limit: int = 1000, offset: int = 0
) -> List[Dict[str, Any]]:
    """
    List entries for a given knowledge base using its ingested_meta.json snapshot.
    Returns list of dicts: { 'path': str, 'hash': str, 'chunk_count': int }
    """
    try:
        project_root = get_project_root()
        working_dir = Path(project_root) / "data" / "vectors" / kb_name
        meta_path = working_dir / "ingested_meta.json"
        if not meta_path.exists():
            log_warning(f"list_kb_entries: metadata file not found: {meta_path}")
            return []
        with open(meta_path, "r", encoding="utf-8") as mf:
            data = json.load(mf)
        if not isinstance(data, dict):
            log_warning("list_kb_entries: invalid metadata format; expected dict")
            return []
        items: List[Dict[str, Any]] = []
        for rel_path, info in data.items():
            if isinstance(info, dict):
                items.append(
                    {
                        "path": rel_path,
                        "hash": info.get("hash"),
                        "chunk_count": info.get("chunk_count", 0),
                    }
                )
            else:
                items.append(
                    {
                        "path": rel_path,
                        "hash": str(info),
                        "chunk_count": 0,
                    }
                )
        start = max(0, int(offset))
        end = start + max(0, int(limit))
        result = items[start:end]
        log_debug(
            f"list_kb_entries('{kb_name}'): {len(result)} items (offset={offset}, limit={limit})"
        )
        return result
    except Exception:
        log_error("list_kb_entries failed", exc_info=True)
        return []


async def create_knowledge_base(kb_name: str) -> Optional[NanoVecDb]:
    """Create a NanoVecDb knowledge base for a specific knowledge base name.

    Args:
        kb_name: Name of the knowledge base (subdirectory name)

    Returns:
        Configured NanoVecDb instance or None if setup fails
    """
    project_root = get_project_root()
    vdb_dir = Path(project_root) / "data" / "vectors"
    try:
        nano_vecdb = NanoVecDb(vdb_dir=str(vdb_dir), kb_name=kb_name)
        await nano_vecdb.initialize()
        return nano_vecdb
    except Exception:
        log_error("Error creating NanoVecDb instance", exc_info=True)
        return None


async def prepare_knowledge_base(
    kb_name: str, contents_db: Optional[Any] = None
) -> Optional[Knowledge]:
    """
    Prepare and maintain the knowledge base, returning an Agno Knowledge object.
    Uses MarkdownReader with MarkdownChunking for ingestion.
    Maintenance principle:
      - If any existing entry is missing/changed, drop and rebuild the whole KB.
      - Otherwise, incrementally skip unchanged files to save embedder tokens.
    """
    try:
        project_root = get_project_root()
        if not project_root:
            log_error("Could not find project root", exc_info=True)
            return None

        # 1) Ensure vector storage
        nano_vecdb = await create_knowledge_base(kb_name)
        if not nano_vecdb:
            log_error(
                f"Failed to create NanoVecDb instance for: {kb_name}", exc_info=True
            )
            return None
        await nano_vecdb.async_create()
        log_info(f"✅ Knowledge base '{kb_name}' storage initialized")

        # 2) Build Knowledge facade on top of vector DB; contents_db 由 AgentOS 统一注入
        knowledge = (
            Knowledge(name=kb_name, vector_db=nano_vecdb, contents_db=contents_db)
            if contents_db
            else Knowledge(name=kb_name, vector_db=nano_vecdb)
        )

        # 3) Scan Markdown files under knowledge/<kb_name>
        knowledge_root = Path(project_root) / "knowledge"
        kb_dir = knowledge_root / kb_name
        if not kb_dir.exists():
            log_warning(f"KB directory not found: {kb_dir}")
            return knowledge

        md_files: List[str] = []
        for r, _, files in os.walk(kb_dir):
            for fn in files:
                if not fn.startswith(".") and fn.endswith(".md"):
                    md_files.append(os.path.join(r, fn))

        if not md_files:
            log_warning(f"No markdown files found in knowledge directory: {kb_name}")
            return knowledge

        # 4) Compute hashes and compare with ingested_meta.json
        file_entries: Dict[str, Dict[str, Any]] = {}
        for fp in md_files:
            with open(fp, "rb") as f:
                b = f.read()
            h = hashlib.sha256(b).hexdigest()
            rel = os.path.relpath(fp, knowledge_root)
            file_entries[rel] = {"path": fp, "hash": h}

        working_dir = Path(project_root) / "data" / "vectors" / kb_name
        meta_path = working_dir / "ingested_meta.json"
        os.makedirs(working_dir, exist_ok=True)

        ingested_meta: Dict[str, Any] = {}
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    data = json.load(mf)
                    if isinstance(data, dict):
                        ingested_meta = data
            except Exception:
                ingested_meta = {}

        # Determine if a full rebuild is needed
        needs_rebuild = False
        skip_ingest = set()
        for rel, old in ingested_meta.items():
            entry = file_entries.get(rel)
            if entry is None:
                # a previously ingested file disappeared -> rebuild
                needs_rebuild = True
                continue
            if isinstance(old, dict) and entry["hash"] == old.get("hash"):
                skip_ingest.add(rel)
            else:
                needs_rebuild = True

        # 5) Rebuild or incremental
        new_ingested_meta: Dict[str, Any] = {}
        if needs_rebuild:
            log_info(
                f"♻️ Detected outdated content in KB '{kb_name}', dropping and rebuilding"
            )
            # remove old metadata (best effort)
            try:
                if meta_path.exists():
                    os.unlink(meta_path)
            except Exception:
                log_error(
                    f"Error deleting old metadata file: {meta_path}", exc_info=True
                )
            await nano_vecdb.async_drop()
            # on rebuild, nothing is skipped
            skip_ingest = set()
        else:
            log_info(
                f"📄 Incremental ingest: total={len(file_entries)} skip={len(skip_ingest)}"
            )

        # 6) Ingest changed/new files using Agno MarkdownReader + MarkdownChunking
        reader = MarkdownReader(
            name="Markdown Chunking Reader",
            chunking_strategy=MarkdownChunking(),
        )

        for rel, info in file_entries.items():
            if rel in skip_ingest:
                log_debug(f"Skipping unchanged: {rel}")
                # keep old meta
                new_ingested_meta[rel] = ingested_meta.get(rel)
                continue

            try:
                log_debug(f"Ingesting: {info['path']} -> {rel}")
                await knowledge.add_content_async(
                    name=rel,
                    path=info["path"],
                    reader=reader,
                )
                # chunk_count unknown without extra queries; store hash and 0
                new_ingested_meta[rel] = {"hash": info["hash"], "chunk_count": 0}
            except Exception:
                log_error(f"❌ Error ingesting {info['path']}", exc_info=True)
                # do not record meta for failed file

        # 7) Flush vector DB once
        try:
            saved = await nano_vecdb.async_flush()
            log_info(f"📦 Vector DB flush saved={saved}")
        except Exception:
            log_error("Vector DB flush failed", exc_info=True)

        # 8) Save metadata snapshot
        try:
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(new_ingested_meta, mf, ensure_ascii=False, indent=2)
            log_info(
                f"📝 Saved ingested_meta.json at {meta_path} with {len(new_ingested_meta)} entries"
            )
        except Exception:
            log_error("Failed to write ingested_meta.json", exc_info=True)

        log_info(f"🎉 Knowledge base ready: {kb_name}")
        return knowledge
    except Exception:
        log_error(
            f"❌ Critical error preparing knowledge base for {kb_name}", exc_info=True
        )
        return None
