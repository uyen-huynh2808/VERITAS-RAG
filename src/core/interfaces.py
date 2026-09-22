from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.core.config import SystemConfig


class BaseContractGate(ABC):
    """
    Interface for document contract validation.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        self.config = config or SystemConfig()

    @abstractmethod
    def validate(
        self,
        metadata: Dict[str, Any],
        content: str,
    ) -> Dict[str, Any]:
        """
        Validate document metadata and content.

        Returns:
            Validation report.
        """
        raise NotImplementedError


class BaseParser(ABC):
    """
    Interface for document parsing.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        self.config = config or SystemConfig()

    @abstractmethod
    def parse(
        self,
        pdf_path: str,
    ) -> Dict[str, Any]:
        """
        Parse a PDF document.

        Returns:
            Parsed document information.
        """
        raise NotImplementedError


class BaseRetriever(ABC):
    """
    Interface for retrieval components.

    Retrieval does not modify global environment state.
    Environment setup is handled by the application entrypoint.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        self.config = config or SystemConfig()

    @abstractmethod
    def retrieve(
        self,
        query: str,
        t_event: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant chunks for a query
        under a temporal event constraint.
        """
        raise NotImplementedError