#!/usr/bin/env python3
"""
AgentOS Instance - agno1

A comprehensive AI agent platform built on the Agno framework with integrated frontend capabilities.
This implementation focuses on development deployment with local file-based storage.
"""

import os

import uvicorn
from agno.utils.log import log_info


def main() -> None:
    """Main entry point for AgentOS."""
    log_info("🤖 Starting AgentOS - agno1")

    # Use factory mode for async app creation
    uvicorn.run(
        app="agno1.app:create_app_factory",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("AGNO_OS_PORT", "7777")),
        reload=os.getenv("AGNO_RELOAD", "false").lower() == "true",
    )


if __name__ == "__main__":
    main()
