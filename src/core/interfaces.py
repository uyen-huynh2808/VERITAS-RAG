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

    def validate_metadata(
        self,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate document metadata before PDF parsing / Bronze ingestion.

        Concrete contract gates may override this method when a separate
        pre-ingestion schema gate is required.

        Returns:
            Validation report.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement validate_metadata()."
        )

    def validate_parsed_document(
        self,
        metadata: Dict[str, Any],
        content: str,
        chunks: List[Dict[str, Any]],
        pdf_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Validate a parsed logical document.

        A logical document may originate from one or multiple physical PDF
        files. The parser is responsible for merging those files into one
        logical document while preserving provenance.

        Args:
            metadata: Logical document metadata.
            content: Parsed/normalized document content.
            chunks: Page-aware parsed chunks.
            pdf_paths: Physical PDF source files belonging to the same
                logical document.

        Returns:
            Validation report.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement "
            "validate_parsed_document()."
        )


class BaseParser(ABC):
    """
    Interface for document parsing.

    A logical legal document may be represented by one or multiple physical
    PDF files. The parser must treat all supplied paths as parts of the same
    logical document and preserve continuous document-page provenance.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        self.config = config or SystemConfig()

    @abstractmethod
    def parse(
        self,
        pdf_paths: List[str],
    ) -> Dict[str, Any]:
        """
        Parse one logical document from one or multiple PDF files.

        Args:
            pdf_paths:
                Ordered list of physical PDF files belonging to the same
                logical document.

        Returns:
            Parsed document information containing, at minimum:
                - metadata
                - markdown
                - chunks
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