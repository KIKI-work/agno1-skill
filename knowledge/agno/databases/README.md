# Database Integration

Agno supports integration with various databases for storing agent state, session data, and knowledge. This section covers database backends, configuration, and implementation patterns.

## Table of Contents

1. [Database Basics](#database-basics)
2. [Supported Databases](#supported-databases)
3. [Configuration Patterns](#configuration-patterns)
4. [Implementation Examples](#implementation-examples)

## Database Basics

Agno provides flexible database integration for persisting agent state, session data, and knowledge. Different database backends offer various trade-offs in terms of performance, scalability, and features.

Basic database configuration:
```python
from agno.db import Database
from agno.agent import Agent

# Configure database connection
db = Database(
    # Database-specific configuration
)

# Use with agent
agent = Agent(
    database=db,
    ...
)
```

## Supported Databases

### Relational Databases

#### PostgreSQL
Full-featured relational database with excellent JSON support:
[PostgreSQL Examples →](../../../../../agno/cookbook/agent_os/dbs/postgresql/)

#### MySQL
Popular open-source relational database:
[MySQL Examples →](../../../../../agno/cookbook/agent_os/dbs/mysql/)

#### SQLite
Lightweight file-based database, good for development:
[SQLite Examples →](../../../../../agno/cookbook/agent_os/dbs/sqlite/)

### NoSQL Databases

#### MongoDB
Document-oriented database with flexible schema:
[MongoDB Examples →](../../../../../agno/cookbook/agent_os/dbs/mongodb/)

#### DynamoDB
Amazon's managed NoSQL database service:
[DynamoDB Examples →](../../../../../agno/cookbook/agent_os/dbs/dynamodb/)

#### Firestore
Google Cloud's NoSQL document database:
[Firestore Examples →](../../../../../agno/cookbook/agent_os/dbs/firestore/)

### Vector Databases

#### Chroma
Open-source embedding database:
[Chroma Examples →](../../../../../agno/cookbook/knowledge/vector_db/chroma/)

#### Pinecone
Managed vector database service:
[Pinecone Examples →](../../../../../agno/cookbook/knowledge/vector_db/pinecone/)

#### Weaviate
Open-source vector search engine:
[Weaviate Examples →](../../../../../agno/cookbook/knowledge/vector_db/weaviate/)

#### Milvus
Open-source vector database for similarity search:
[Milvus Examples →](../../../../../agno/cookbook/knowledge/vector_db/milvus/)

#### Qdrant
Vector similarity search engine:
[Qdrant Examples →](../../../../../agno/cookbook/knowledge/vector_db/qdrant/)

## Configuration Patterns

### Connection Configuration
Setting up database connections:
[Configuration Examples →](../../../../../agno/cookbook/agent_os/dbs/)

### Schema Design
Designing database schemas for agent data:
[Schema Examples →](../../../../../agno/cookbook/agent_os/dbs/)

### Performance Tuning
Optimizing database performance:
[Performance Examples →](../../../../../agno/cookbook/knowledge/vector_db/)

### Migration Strategies
Handling database schema changes:
[Migration Examples →](../../../../../agno/cookbook/agent_os/dbs/)

## Implementation Examples

### Agent OS Examples
1. [PostgreSQL](../../../../../agno/cookbook/agent_os/dbs/postgresql/)
2. [MySQL](../../../../../agno/cookbook/agent_os/dbs/mysql/)
3. [SQLite](../../../../../agno/cookbook/agent_os/dbs/sqlite/)
4. [MongoDB](../../../../../agno/cookbook/agent_os/dbs/mongodb/)
5. [DynamoDB](../../../../../agno/cookbook/agent_os/dbs/dynamodb/)
6. [Firestore](../../../../../agno/cookbook/agent_os/dbs/firestore/)

### Knowledge Examples
1. [Chroma](../../../../../agno/cookbook/knowledge/vector_db/chroma/)
2. [Pinecone](../../../../../agno/cookbook/knowledge/vector_db/pinecone/)
3. [Weaviate](../../../../../agno/cookbook/knowledge/vector_db/weaviate/)
4. [Milvus](../../../../../agno/cookbook/knowledge/vector_db/milvus/)
5. [Qdrant](../../../../../agno/cookbook/knowledge/vector_db/qdrant/)
6. [PGVector](../../../../../agno/cookbook/knowledge/vector_db/pgvector/)

## Best Practices

1. **Choose the Right Database**: Select based on your specific requirements (relational vs. document vs. vector)
2. **Connection Management**: Properly manage database connections and pooling
3. **Indexing**: Create appropriate indexes for performance
4. **Backup Strategy**: Implement regular backup procedures
5. **Security**: Secure database connections and access controls
6. **Monitoring**: Monitor database performance and usage

## Related Topics

- [Knowledge Management](../knowledge/README.md) - Vector database usage for knowledge
- [Core Concepts](../core-concepts/README.md) - State persistence
- [Agents](../agents/README.md) - Agent data storage
- [Teams](../teams/README.md) - Team data sharing