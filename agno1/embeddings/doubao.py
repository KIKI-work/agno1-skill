"""Doubao embedding implementation for agno1."""

__all__ = ["DoubaoEmbedding"]

import os
from typing import Any, List, Optional, Union

import numpy as np
from volcenginesdkarkruntime import Ark


class DoubaoEmbedding:
    """Doubao embedding model implementation using official Volcengine ARK SDK."""

    def __init__(
        self,
        model: str = "doubao-embedding-large-text-250515",
        api_key: Optional[str] = None,
        dimensions: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize Doubao embedding model.

        Args:
            model: The Doubao embedding model name
            api_key: ARK API key, defaults to ARK_API_KEY environment variable
            dimensions: Optional dimension reduction (2048, 1024, 512, 256)
            **kwargs: Additional arguments
        """
        self.model = model
        self.api_key = api_key or os.getenv("ARK_API_KEY")
        self.dimensions = dimensions
        self.enable_batch = True  # Add the missing enable_batch attribute

        if not self.api_key:
            raise ValueError(
                "ARK_API_KEY environment variable or api_key parameter is required"
            )

        # Validate dimensions if provided
        if self.dimensions is not None and self.dimensions not in [
            2048,
            1024,
            512,
            256,
        ]:
            raise ValueError("dimensions must be one of: 2048, 1024, 512, 256")

        # Initialize ARK client
        self.client = Ark(api_key=self.api_key)

    async def get_embedding(self, text: Union[str, List[str]]) -> List[List[float]]:
        """Get embeddings for text(s).

        Args:
            text: Single text string or list of text strings

        Returns:
            List of embedding vectors
        """
        # Ensure input is a list
        if isinstance(text, str):
            texts = [text]
        else:
            texts = text

        return await self._encode(texts, is_query=False)

    async def get_query_embedding(
        self, query: Union[str, List[str]]
    ) -> List[List[float]]:
        """Get embeddings for query text(s) with query instruction.

        Args:
            query: Single query string or list of query strings

        Returns:
            List of embedding vectors optimized for query
        """
        # Ensure input is a list
        if isinstance(query, str):
            queries = [query]
        else:
            queries = query

        return await self._encode(queries, is_query=True)

    async def _encode(
        self, inputs: List[str], is_query: bool = False
    ) -> List[List[float]]:
        """Encode texts to embeddings using ARK SDK.

        Args:
            inputs: List of input texts
            is_query: Whether to use query instruction for optimal performance

        Returns:
            List of embedding vectors as lists of floats
        """
        # Apply query instruction if needed
        processed_inputs = inputs.copy()
        if is_query:
            # Use instruction for optimal performance, tuned for web search tasks
            # Reference: https://github.com/embeddings-benchmark/mteb/blob/main/mteb/models/seed_models.py
            processed_inputs = [
                f"Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: {text}"
                for text in inputs
            ]

        # Call Doubao embedding API using ARK SDK
        try:
            response = self.client.embeddings.create(
                model=self.model, input=processed_inputs, encoding_format="float"
            )

            # Extract embeddings and convert to numpy arrays
            embeddings = []
            for item in response.data:
                embedding = np.array(item.embedding, dtype=np.float32)

                # Apply dimension reduction if specified
                if self.dimensions is not None:
                    embedding = embedding[: self.dimensions]

                # Normalize embeddings for cosine similarity computation
                embedding = embedding / np.linalg.norm(embedding)

                # Convert back to list
                embeddings.append(embedding.tolist())

            return embeddings

        except Exception as e:
            raise RuntimeError(f"Failed to call Doubao embedding API: {e}")

    @property
    def embedding_dimension(self) -> int:
        """Get the embedding dimension."""
        if self.dimensions is not None:
            return self.dimensions
        # Default dimension for doubao-embedding-large-text-250515
        return 2048
