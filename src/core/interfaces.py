from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.core.config import SystemConfig


class BaseContractGate(ABC):

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        self.config = config or SystemConfig()

    @abstractmethod
    def validate_metadata(
        self,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Schema-stage validation.

        This can be executed before the parsed content
        is admitted to the downstream pipeline.
        """
        raise NotImplementedError

    @abstractmethod
    def validate(
        self,
        metadata: Dict[str, Any],
        content: str,
        chunks: Optional[
            List[Dict[str, Any]]
        ] = None,
        tables: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Full document-level validation.
        """
        raise NotImplementedError


class BaseParser(ABC):

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        self.config = config or SystemConfig()

    @abstractmethod
    def parse(
        self,
        parts_paths: List[str],
        logical_doc_id: str,
    ) -> Any:
        """
        Parse a logical legal document that may consist
        of one or multiple physical PDF parts.
        """
        raise NotImplementedError


class BaseRetriever(ABC):

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
        raise NotImplementedError