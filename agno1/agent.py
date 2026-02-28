"""Compatibility wrapper for agent setup.

Agent definitions live in the top-level agents/ directory.
"""

__all__ = ["setup_agents", "AgentBundle"]

from agents import AgentBundle, setup_agents
