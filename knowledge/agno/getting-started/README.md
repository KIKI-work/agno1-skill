# Getting Started with Agno

This guide will help you quickly get up and running with the Agno framework. We'll walk through the basics of setting up your environment, creating your first agent, and building more complex systems.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Your First Agent](#your-first-agent)
4. [Agent with Tools](#agent-with-tools)
5. [Agent with Knowledge](#agent-with-knowledge)
6. [Next Steps](#next-steps)

## Prerequisites

Before you begin, ensure you have the following installed:

- Python 3.12 or higher
- UV package manager
- Git (for accessing cookbook examples)

For full development capabilities, you may also want:
- Node.js 18+ and pnpm (for frontend development)
- Access to AI model APIs (OpenAI, Anthropic, etc.)

## Installation

### Basic Installation

```bash
# Create a new project directory
mkdir my-agno-project
cd my-agno-project

# Initialize with UV
uv init

# Add Agno as a dependency
uv add agno
```

### Development Installation

For development with examples from the cookbook:

```bash
# Clone the Agno repository with cookbook examples
git clone https://github.com/agno-agi/agno.git

# Navigate to the cookbook directory
cd agno/cookbook
```

## Your First Agent

Let's create a simple agent that can answer questions:

```python
from agno.agent import Agent
from agno.models.openai import OpenAIChat

# Create an agent with a simple instruction
agent = Agent(
    model=OpenAIChat(id="gpt-4"),
    instructions="You are a helpful assistant that answers questions clearly and concisely."
)

# Run the agent
response = agent.run("What is the capital of France?")
print(response.content)
```

[View full example →](../../../../../agno/cookbook/getting_started/01_basic_agent.py)

## Agent with Tools

Agents become more powerful when equipped with tools. Here's an example of an agent with a calculator tool:

```python
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools import Toolkit
from agno.tools.calculator import Calculator

# Create a toolkit with a calculator
toolkit = Toolkit(tools=[Calculator()])

# Create an agent with the toolkit
agent = Agent(
    model=OpenAIChat(id="gpt-4"),
    instructions="You are a helpful assistant that can use tools to solve math problems.",
    toolkits=[toolkit]
)

# Run the agent
response = agent.run("What is 15 multiplied by 23?")
print(response.content)
```

[View full example →](../../../../../agno/cookbook/getting_started/02_agent_with_tools.py)

## Agent with Knowledge

Agents can access external knowledge through Retrieval-Augmented Generation (RAG):

```python
from agno.agent import Agent
from agno.knowledge import KnowledgeBase
from agno.models.openai import OpenAIChat

# Create a knowledge base from a URL
knowledge = KnowledgeBase.from_url(
    url="https://en.wikipedia.org/wiki/Artificial_intelligence"
)

# Create an agent with knowledge
agent = Agent(
    model=OpenAIChat(id="gpt-4"),
    instructions="You are an expert on artificial intelligence. Use the provided knowledge to answer questions.",
    knowledge=knowledge
)

# Run the agent
response = agent.run("What are the main approaches to artificial intelligence?")
print(response.content)
```

[View full example →](../../../../../agno/cookbook/getting_started/03_agent_with_knowledge.py)

## Next Steps

After mastering the basics, explore these advanced topics:

1. [Agents](../agents/README.md) - Learn about different agent types and capabilities
2. [Teams](../teams/README.md) - Build collaborative multi-agent systems
3. [Workflows](../workflows/README.md) - Create complex automated processes
4. [Knowledge Management](../knowledge/README.md) - Implement advanced RAG systems
5. [Tools & Integrations](../tools/README.md) - Extend agent capabilities with custom tools

## Additional Resources

- [Agno Cookbook Examples](../../../../../agno/cookbook/) - Comprehensive examples for all features
- [API Documentation](https://docs.agno.com/) - Detailed reference for all classes and methods
- [Community Forum](https://github.com/agno-agi/agno/discussions) - Get help from other developers