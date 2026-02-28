"""
NanoVecDb implementation for vector database operations.

This module provides a simplified vector database implementation following
the Agno VectorDb interface, focusing on embedding and storage
functionality without LLM dependencies.
"""

__all__ = [
    "NanoVecDb",
]

import json
import os
from typing import Any, Dict, List, Optional

import numpy as np
from agno.knowledge.document import Document
from agno.utils.log import log_debug, log_error, log_info, log_warning
from agno.vectordb.base import VectorDb
from lightrag.kg.nano_vector_db_impl import NanoVectorDBStorage
from lightrag.kg.shared_storage import initialize_share_data
from lightrag.utils import EmbeddingFunc, wrap_embedding_func_with_attrs
from numpy.typing import NDArray

from .embeddings.doubao import DoubaoEmbedding

# 初始化 LightRAG 共享存储
initialize_share_data(1)


class NanoVecDb(VectorDb):
    """
    Vector DB 实现，基于 LightRAG 的 NanoVectorDBStorage，仅做嵌入与检索，不依赖 LLM。
    """

    def __init__(
        self,
        vdb_dir: str,
        kb_name: str,
        embedding_func: Optional[EmbeddingFunc] = None,
        global_config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.vdb_dir = vdb_dir
        self.kb_name = kb_name
        self.working_dir = os.path.join(self.vdb_dir, self.kb_name)
        self.global_config = (global_config or {}).copy()

        # 确保目录存在
        os.makedirs(self.working_dir, exist_ok=True)

        # 必要全局配置
        root_dir = os.path.abspath(self.vdb_dir)
        workspace_name = self.kb_name
        self.global_config.setdefault("working_dir", root_dir)
        self.global_config.setdefault("embedding_batch_num", 32)
        # 阈值说明：
        # 1) lightrag 在存储层按 cosine_better_than_threshold 做“硬过滤”
        # 2) 当前语料分布下相似度整体偏低（英问英答最高分约 ~0.046），为了保证最低限度的召回，
        #    这里暂时将默认阈值设为 0.01。后续可根据新语料再评估调高。
        self.global_config.setdefault(
            "vector_db_storage_cls_kwargs", {"cosine_better_than_threshold": 0.01}
        )

        self._vector_storage = NanoVectorDBStorage(
            namespace="default",
            workspace=workspace_name,
            global_config=self.global_config,
            embedding_func=embedding_func or self._create_default_embedding_func(),
        )

        # 持久化需要的元字段
        self._vector_storage.meta_fields = {
            "content",
            "file_path",
            "source",
            "file_hash",
        }

        self.expected_storage_file = os.path.join(self.working_dir, "vdb_default.json")
        log_info(f"NanoVecDb storage expect file: {self.expected_storage_file}")
        log_debug(f"NanoVecDb global_config: {self.global_config}")
        log_info(
            f"Initialized NanoVecDb with working directory: {self.working_dir} "
            f"(vdb_dir={self.vdb_dir}, kb={self.kb_name})"
        )

    async def initialize(self):
        await self._vector_storage.initialize()

    def _create_default_embedding_func(self) -> EmbeddingFunc:
        """使用 DoubaoEmbedding 创建默认 EmbeddingFunc。"""
        try:
            doubao_embedding = DoubaoEmbedding()
            # 保留引用，检索阶段使用 query 指令增强
            self._doubao_embedding = doubao_embedding

            async def _call(
                texts: List[str], _priority: int = 0
            ) -> NDArray[np.float32]:
                vecs: List[List[float]] = []
                for t in texts:
                    emb = await doubao_embedding.get_embedding(t)
                    vecs.append(emb[0])
                return np.asarray(vecs, dtype=np.float32)

            return wrap_embedding_func_with_attrs(
                embedding_dim=doubao_embedding.embedding_dimension
            )(_call)
        except Exception:
            log_error("Failed to create DoubaoEmbedding", exc_info=True)
            raise

    # 兼容辅助
    def exists(self) -> bool:
        return os.path.exists(self.working_dir) and os.path.isdir(self.working_dir)

    def create(self) -> None:
        os.makedirs(self.working_dir, exist_ok=True)

    async def async_create(self) -> None:
        os.makedirs(self.working_dir, exist_ok=True)

    def name_exists(self, name: str) -> bool:
        return False

    def async_name_exists(self, name: str) -> bool:
        return False

    def id_exists(self, id: str) -> bool:
        return False

    # VectorDb 接口实现（写入）
    def content_hash_exists(self, content_hash: str) -> bool:
        return False

    def insert(
        self,
        content_hash: str,
        documents: List[Document],
        filters: Optional[Dict[str, Any]] = None,
    ) -> None:
        log_warning(
            "NanoVecDb.insert called; sync insert is not supported. Use async_insert instead."
        )
        raise NotImplementedError(
            "NanoVecDb.insert is not supported. Please call async_insert(...)"
        )

    async def async_insert(
        self,
        content_hash: str,
        documents: List[Document],
        filters: Optional[Dict[str, Any]] = None,
    ) -> None:
        data: Dict[str, Dict[str, Any]] = {}
        for i, doc in enumerate(documents):
            doc_id = f"{content_hash}_{i}"
            entry: Dict[str, Any] = {"content": doc.content}
            meta = doc.meta_data or {}
            source = None
            if isinstance(meta, dict):
                source = meta.get("source") or meta.get("file_path")
            if source:
                entry["source"] = source
                entry["file_path"] = source
            entry["file_hash"] = content_hash
            data[doc_id] = entry
        if data:
            await self._vector_storage.upsert(data)

    def upsert(
        self,
        content_hash: str,
        documents: List[Document],
        filters: Optional[Dict[str, Any]] = None,
    ) -> None:
        log_warning(
            "NanoVecDb.upsert called; sync upsert is not supported. Use async_upsert instead."
        )
        raise NotImplementedError(
            "NanoVecDb.upsert is not supported. Please call async_upsert(...)"
        )

    async def async_upsert(
        self,
        content_hash: str,
        documents: List[Document],
        filters: Optional[Dict[str, Any]] = None,
    ) -> None:
        # 与 Knowledge.add_content_async 对齐：
        # - 使用来源(优先 meta.source，其次 meta.file_path，再次 content_hash) + chunk 序号作为 doc_id，确保可覆盖更新
        data: Dict[str, Dict[str, Any]] = {}
        for i, doc in enumerate(documents):
            meta = doc.meta_data or {}
            src_name: Optional[str] = None
            file_path: Optional[str] = None
            if isinstance(meta, dict):
                src_name = meta.get("source")
                file_path = meta.get("file_path")
            id_source = src_name or file_path or content_hash
            doc_id = f"{id_source}_chunk_{i}"

            entry: Dict[str, Any] = {"content": doc.content, "file_hash": content_hash}
            if src_name:
                entry["source"] = src_name
            if file_path:
                entry["file_path"] = file_path
            elif src_name:
                # 若没有 file_path，至少回填一个与 source 一致的可追踪路径
                entry["file_path"] = src_name

            # 合并 filters 中可能相关的元字段（仅当尚未设置且属于已配置的 meta_fields）
            if isinstance(filters, dict):
                if "source" in filters and "source" not in entry:
                    entry["source"] = filters["source"]
                if "file_path" in filters and "file_path" not in entry:
                    entry["file_path"] = filters["file_path"]

            data[doc_id] = entry
        if data:
            await self._vector_storage.upsert(data)

    # VectorDb 检索
    def search(
        self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        # 同步 search 不实现，使用 async_search
        log_warning(
            "NanoVecDb.search called; sync search is not supported. Use async_search instead."
        )
        raise NotImplementedError(
            "NanoVecDb.search is not supported. Please call async_search(...)"
        )

    async def async_search(
        self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """无条件：先嵌入 -> L2 归一化 -> 以 list[float] 作为 query_embedding 调用 storage.query。"""
        if query is None or (isinstance(query, str) and query.strip() == ""):
            return []  # 空查询不匹配任何内容

        # 先嵌入（检索阶段使用 query 指令的嵌入以提升相似度）
        try:
            if hasattr(self, "_doubao_embedding"):
                emb = await self._doubao_embedding.get_query_embedding(query)  # type: ignore
            else:
                emb = await self._vector_storage.embedding_func([query])
        except Exception:
            # 兜底：使用存储的 embedding_func
            emb = await self._vector_storage.embedding_func([query])
        if not hasattr(emb, "__len__") or len(emb) == 0:
            log_error("Embedding returned empty batch")
            raise RuntimeError(
                "NanoVecDb.async_search: Embedding returned empty batch for query"
            )
        qvec = emb[0]
        if qvec is None or (hasattr(qvec, "__len__") and len(qvec) == 0):
            log_error("Embedding returned empty vector")
            raise RuntimeError(
                "NanoVecDb.async_search: Embedding returned empty vector for query"
            )

        # L2 归一化并转 list[float]
        try:
            qarr = np.asarray(qvec, dtype=np.float32)
            norm = np.linalg.norm(qarr)
            if norm > 0:
                qarr = qarr / norm
            qembed: List[float] = qarr.tolist()
        except Exception:
            qembed = list(qvec) if hasattr(qvec, "__iter__") else qvec  # type: ignore

        # 以 query_embedding 调用底层检索（阈值在 storage 内部生效）
        rows: List[Dict[str, Any]] = await self._vector_storage.query(
            query, limit, qembed
        )
        log_debug(f"NanoVecDb.async_search: rows={len(rows)} query='{query}'")

        docs: List[Document] = []
        for r in rows:
            content = r.get("content", "")
            try:
                doc = Document(content=content)
                meta: Dict[str, Any] = {}
                if r.get("source"):
                    meta["source"] = r.get("source")
                if r.get("file_path"):
                    meta["file_path"] = r.get("file_path")
                doc.meta_data = meta
                if r.get("id"):
                    doc.id = r.get("id")
                docs.append(doc)
            except Exception:
                log_error(
                    "NanoVecDb.async_search: Document construct failed", exc_info=True
                )
                continue

        log_debug(f"NanoVecDb.async_search: returned={len(docs)} query='{query}'")
        return docs

    async def async_drop(self) -> None:
        await self._vector_storage.drop()
        log_info("All data dropped from NanoVecDb (async)")

    async def async_exists(self) -> bool:
        return self.exists()

    def delete(self) -> bool:
        return False

    def delete_by_id(self, id: str) -> bool:
        return False

    def delete_by_name(self, name: str) -> bool:
        raise RuntimeError(
            "delete_by_name is not supported by NanoVecDb/NanoVectorDBStorage"
        )

    def delete_by_metadata(self, metadata: Dict[str, Any]) -> bool:
        return False

    def update_metadata(self, content_id: str, metadata: Dict[str, Any]) -> None:
        log_warning("update_metadata not supported for NanoVecDb")
        raise NotImplementedError("NanoVecDb.update_metadata is not supported")

    def delete_by_content_id(self, content_id: str) -> bool:
        return False

    async def async_flush(self) -> bool:
        try:
            saved = await self._vector_storage.index_done_callback()
            log_info(f"NanoVecDb.flush saved={saved} file={self.expected_storage_file}")
            return bool(saved)
        except Exception:
            log_error("NanoVecDb.flush failed", exc_info=True)
            return False

    def drop(self) -> None:
        log_warning(
            "NanoVecDb.drop called; sync drop is not supported. Use async_drop instead."
        )
        raise NotImplementedError(
            "NanoVecDb.drop is not supported. Please call async_drop(...)"
        )
