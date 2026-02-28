# Doubao Embedding Integration

This document describes the Doubao embedding implementation in agno1, providing vector embedding capabilities using ByteDance's Doubao models through the official Volcengine ARK SDK.

## Overview

The Doubao embedding integration allows agno1 to generate high-quality vector embeddings for text processing, semantic search, and retrieval tasks. It uses ByteDance's Doubao embedding models through their official ARK SDK for reliable and efficient API access. <mcreference link="https://seed1-5-embedding.github.io/" index="1">1</mcreference>

## Features

- **High-quality embeddings**: Uses Doubao's state-of-the-art embedding models
- **Official SDK**: Built on the official Volcengine ARK SDK for reliability
- **Query optimization**: Automatic query instruction for improved retrieval performance
- **Dimension reduction**: Configurable output dimensions (2048, 1024, 512, 256)
- **Normalization**: Automatic L2 normalization for cosine similarity
- **Batch processing**: Support for single texts or batches of texts

## Setup

### 1. Environment Variables

Set your ARK API key:

```bash
export ARK_API_KEY="your_ark_api_key_here"
```

### 2. Dependencies

The following dependencies are automatically installed:

- `volcengine-python-sdk[ark]>=4.0.24` - Official Volcengine ARK SDK
- `numpy>=1.21.0` - For numerical operations

## Usage

### Basic Usage

```python
from agno1.embeddings import DoubaoEmbedding

# Initialize the embedding model
embedder = DoubaoEmbedding(
    model="doubao-embedding-large-text-250515",
    dimensions=1024  # Optional: reduce dimensions
)

# Generate embeddings for documents
texts = ["花椰菜又称菜花、花菜，是一种常见的蔬菜。"]
embeddings = embedder.get_embedding(texts)

# Generate embeddings for queries (with optimization)
queries = ["什么是花椰菜？"]
query_embeddings = embedder.get_query_embedding(queries)
```

### Integration with Agent

The Doubao embedding is automatically integrated with the agno1 agent when the `ARK_API_KEY` environment variable is set:

```python
# In agent.py - automatic integration
if os.getenv("ARK_API_KEY"):
    embedder = DoubaoEmbedding(
        model="doubao-embedding-large-text-250515",
        dimensions=1024
    )
    # Pass to Agent initialization
    agent = Agent(
        model=model,
        db=db,
        tools=tools,
        embedder=embedder,  # Enhanced semantic capabilities
        # ... other config
    )
```

## Configuration Options

### Model Selection

Currently supported model:

- `doubao-embedding-large-text-250515` (default) - Large text embedding model

### Dimension Reduction

You can reduce embedding dimensions for efficiency: <mcreference link="https://seed1-5-embedding.github.io/" index="1">1</mcreference>

```python
embedder = DoubaoEmbedding(
    dimensions=512  # Options: 2048 (default), 1024, 512, 256
)
```

### Custom API Key

```python
embedder = DoubaoEmbedding(
    api_key="your_custom_key"
)
```

## Query Optimization

The implementation automatically applies query instructions for better retrieval performance: <mcreference link="https://seed1-5-embedding.github.io/" index="1">1</mcreference>

```python
# For documents - no instruction
doc_embeddings = embedder.get_embedding(["Document text"])

# For queries - automatic instruction applied
query_embeddings = embedder.get_query_embedding(["Search query"])
```

The query instruction used:

```
Instruct: Given a web search query, retrieve relevant passages that answer the query
Query: {your_query}
```

## SDK Implementation Details

The implementation uses the official Volcengine ARK SDK: <mcreference link="https://seed1-5-embedding.github.io/" index="1">1</mcreference>

```python
from volcenginesdkarkruntime import Ark

# Initialize client
client = Ark(api_key=os.getenv("ARK_API_KEY"))

# Create embeddings
response = client.embeddings.create(
    model="doubao-embedding-large-text-250515",
    input=texts,
    encoding_format="float"
)
```

## API Response Format

The embedding API returns normalized vectors as lists of floats:

```python
embeddings = embedder.get_embedding(["text"])
# Returns: [[0.061, 0.017, 0.040, ...]]  # List of normalized float vectors
```

## Error Handling

The implementation includes comprehensive error handling:

- **Missing API Key**: Raises `ValueError` if `ARK_API_KEY` is not set
- **Invalid Dimensions**: Raises `ValueError` for unsupported dimension values
- **SDK Errors**: Raises `RuntimeError` for SDK or API response issues

## Testing

Run the test script to verify your setup:

```bash
python test_doubao_embedding.py
```

Expected output:

```
✅ ARK_API_KEY found
🔧 Initializing Doubao embedding...
✅ Doubao embedding initialized successfully
📝 Testing text embedding...
✅ Text embedding generated: shape=1x1024
🔍 Testing query embedding...
✅ Query embedding generated: shape=1x1024
✅ Embedding dimensions correct: 1024
🎉 All tests passed!
```

## Performance Considerations

- **Batch Processing**: Process multiple texts in a single API call for efficiency
- **Dimension Reduction**: Use smaller dimensions (512, 256) for faster processing
- **Caching**: Consider caching embeddings for frequently used texts
- **Rate Limits**: Be aware of API rate limits when processing large volumes
- **SDK Benefits**: Official SDK provides better error handling and retry logic

## Troubleshooting

### Common Issues

1. **ModuleNotFoundError**: Ensure dependencies are installed with `uv sync`
2. **API Key Error**: Verify `ARK_API_KEY` is set correctly
3. **Network Errors**: Check internet connection and API endpoint availability
4. **Dimension Errors**: Use only supported dimensions: 2048, 1024, 512, 256
5. **SDK Import Error**: Ensure `volcengine-python-sdk[ark]` is properly installed

### Debug Mode

Enable debug logging to troubleshoot issues:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

embedder = DoubaoEmbedding(...)
```

## Integration Examples

### Semantic Search

```python
# Index documents
documents = ["Document 1 text", "Document 2 text", ...]
doc_embeddings = embedder.get_embedding(documents)

# Search with query
query = "search query"
query_embedding = embedder.get_query_embedding([query])[0]

# Calculate similarities (cosine similarity with normalized vectors)
import numpy as np
similarities = np.dot(doc_embeddings, query_embedding)
best_match_idx = np.argmax(similarities)
```

### Vector Database Integration

```python
# Prepare embeddings for vector database
texts = ["text1", "text2", "text3"]
embeddings = embedder.get_embedding(texts)

# Store in vector database (example with any vector DB)
for text, embedding in zip(texts, embeddings):
    vector_db.insert(text=text, vector=embedding)
```

## Migration from HTTP Implementation

If you're migrating from a custom HTTP implementation:

1. **Dependencies**: Replace `requests` with `volcengine-python-sdk[ark]`
2. **Initialization**: Remove `base_url` parameter, SDK handles endpoints
3. **API Calls**: Replace HTTP requests with SDK method calls
4. **Error Handling**: SDK provides better structured error handling
5. **Testing**: Existing tests should work without modification

## License

This implementation follows the same license as the agno1 project.
