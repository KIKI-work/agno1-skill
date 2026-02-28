# Core Concepts

Understanding the fundamental concepts of the Agno framework is essential for building effective AI systems. This section covers the core abstractions that form the foundation of Agno-based applications.

## Table of Contents

1. [Agents](#agents)
2. [Teams](#teams)
3. [Workflows](#workflows)
4. [Knowledge](#knowledge)
5. [Tools](#tools)
6. [Sessions](#sessions)
7. [State Management](#state-management)
8. [Memory](#memory)

## Agents

Agents are the fundamental building blocks of the Agno framework. An agent is an AI entity with specific capabilities, tools, and instructions that can perform tasks autonomously or with human guidance.

Key characteristics of agents:
- **Autonomous**: Can make decisions and take actions independently
- **Tool-enabled**: Can interact with external systems through tools
- **Context-aware**: Maintain context through sessions and memory
- **Extensible**: Can be customized with specific instructions and capabilities

[Learn more about agents →](../agents/README.md)

## Teams

Teams are collaborative groups of agents working together to accomplish complex tasks. Teams provide a higher-level abstraction for coordinating multiple agents and distributing work among them.

Key characteristics of teams:
- **Collaborative**: Multiple agents working toward a common goal
- **Coordinated**: Structured communication and task distribution
- **Scalable**: Can include any number of agents or sub-teams
- **Flexible**: Support various coordination patterns

[Learn more about teams →](../teams/README.md)

## Workflows

Workflows are predefined sequences of steps that automate complex processes. Each step in a workflow can be executed by an agent, a team, or a custom function.

Key characteristics of workflows:
- **Structured**: Defined sequence of steps with clear execution paths
- **Conditional**: Support branching logic and decision-making
- **Reusable**: Can be executed multiple times with different inputs
- **Traceable**: Provide clear audit trails of execution steps

[Learn more about workflows →](../workflows/README.md)

## Knowledge

Knowledge systems enable agents to access and utilize information at runtime. The Agno framework provides robust support for Retrieval-Augmented Generation (RAG) systems.

Key characteristics of knowledge systems:
- **Searchable**: Content can be searched and retrieved based on relevance
- **Multi-source**: Support various data sources (files, URLs, databases)
- **Embeddings**: Use vector embeddings for semantic search
- **Filters**: Apply filters to refine search results

[Learn more about knowledge management →](../knowledge/README.md)

## Tools

Tools are utilities that allow agents to perform specific tasks such as searching the web, running SQL queries, sending emails, or calling APIs.

Key characteristics of tools:
- **Actionable**: Enable agents to interact with external systems
- **Declarative**: Defined with clear input/output specifications
- **Secure**: Support authentication and access control
- **Extensible**: Can be custom-built for specific requirements

[Learn more about tools →](../tools/README.md)

## Sessions

Sessions provide context persistence across multiple interactions with an agent. They maintain conversation history, state information, and other contextual data.

Key characteristics of sessions:
- **Persistent**: Maintain state across multiple requests
- **Organized**: Group related interactions together
- **Manageable**: Can be renamed, cached, or stored
- **Traceable**: Provide audit trails of agent interactions

## State Management

State management allows agents and teams to maintain and update information throughout their lifecycle. This includes both session-level state and shared state across team members.

Key characteristics of state management:
- **Dynamic**: State can be updated during execution
- **Shared**: Can be accessed by multiple agents in a team
- **Structured**: Supports complex data structures
- **Persistent**: Can be stored and retrieved

## Memory

Memory systems allow agents to store insights and facts about users learned through conversations. This personalization capability enhances the quality of agent responses over time.

Key characteristics of memory systems:
- **Personalized**: Store user-specific information
- **Persistent**: Retain information across sessions
- **Selective**: Can filter and prioritize memories
- **Privacy-aware**: Support data protection and deletion