"""Utility functions for AgentOS."""

__all__ = [
    "get_project_root",
    "get_relative_path",
    "get_network_ips",
    "display_access_info",
]

import os
import socket
from typing import List

from agno.utils.log import log_info


def get_project_root() -> str:
    """Get the project root directory."""
    # Get the directory containing this file (agno1/agno1/)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level to get to project root (agno1/)
    project_root = os.path.dirname(current_dir)
    return project_root


def get_relative_path(file_path: str, base_prefix: str = "/knowledge/") -> str:
    """Get relative path from project root, with specified prefix.

    Args:
        file_path: Absolute path to the file
        base_prefix: Prefix to use for relative paths (default: "/knowledge/")

    Returns:
        Relative path starting with the specified prefix
    """
    project_root = get_project_root()
    try:
        # Get relative path from project root
        rel_path = os.path.relpath(file_path, project_root)
        # Ensure it starts with knowledge/ and convert to specified prefix format
        if rel_path.startswith("knowledge/"):
            return base_prefix + rel_path[len("knowledge/") :]
        else:
            # Fallback to absolute path if not in knowledge directory
            return file_path
    except ValueError:
        # If paths are on different drives (Windows), return absolute path
        return file_path


def get_network_ips() -> List[str]:
    """Get all network IP addresses for this machine using socket connections."""
    ips = []

    try:
        # This connects to a public DNS server to determine the local IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Connect to Google's public DNS (doesn't actually send data)
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
            if primary_ip and primary_ip != "127.0.0.1":
                ips.append(primary_ip)
    except Exception as e:
        from agno.utils.log import log_error

        log_error("utils: error in function", exc_info=True)

    return ips


def display_access_info() -> None:
    """Display access information with multiple URLs."""
    log_info("🚀 AgentOS Server Started!")
    log_info("=" * 50)

    port = int(os.getenv("AGNO_OS_PORT", "7777"))
    security_key = os.getenv("OS_SECURITY_KEY")

    log_info(f"  WebUI available at: http://127.0.0.1:{port}/")

    # Local access
    local_url = f"http://127.0.0.1:{port}"
    if security_key:
        local_auth_url = f"{local_url}?auth={security_key}"
        log_info(f"🔐 Local with Auth: {local_auth_url}")
    else:
        log_info(f"🏠 Local Access: {local_url}")

    # Network access
    network_ips = get_network_ips()
    if network_ips:
        log_info("🌐 Network Access:")
        for ip in network_ips:
            network_url = f"http://{ip}:{port}"
            if security_key:
                network_auth_url = f"{network_url}?auth={security_key}"
                log_info(f"   🔐 {network_auth_url}")
            else:
                log_info(f"   {network_url}")
