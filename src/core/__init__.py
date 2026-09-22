"""Core module containing system configurations, environment setup,
base interfaces, and component factory.
"""

from src.core.config import (
    SystemConfig,
    DatabaseConfig,
    ContractConfig,
    ModelConfig,
    load_system_config,
)

from src.core.environment import setup_environment

from src.core.interfaces import (
    BaseContractGate,
    BaseParser,
    BaseRetriever,
)

from src.core.factory import ComponentFactory


__all__ = [
    "SystemConfig",
    "DatabaseConfig",
    "ContractConfig",
    "ModelConfig",
    "load_system_config",
    "setup_environment",
    "BaseContractGate",
    "BaseParser",
    "BaseRetriever",
    "ComponentFactory",
]