"""
Alan - Personal AI Operating System
A modular, privacy-first AI companion with agent orchestration, memory, tools, and voice.
"""

__version__ = "0.1.0"
__author__ = "Alan Team"
__email__ = "team@alan.ai"
__license__ = "MIT"

# Core exports
from alan.config import get_settings, Settings
from alan.core import AlanKernel, get_kernel
from alan.logging import setup_logging, get_logger

__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "__license__",
    "get_settings",
    "Settings",
    "AlanKernel",
    "get_kernel",
    "setup_logging",
    "get_logger",
]