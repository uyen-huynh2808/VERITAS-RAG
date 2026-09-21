from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseContractGate(ABC):
    """Abstract interface for the 4-layer Executable Data Contract Gate."""
    
    @abstractmethod
    def validate(self, metadata: Dict[str, Any], content: str) -> Dict[str, Any]:
        """Validate input document metadata and content against predefined contract rules.
        
        Args:
            metadata (Dict[str, Any]): Raw document metadata attributes.
            content (str): Parsed Markdown text content.
            
        Returns:
            Dict[str, Any]: Validation status ('PASSED' or 'QUARANTINE') and diagnostic report.
        """
        pass


class BaseParser(ABC):
    """Abstract interface for hybrid PDF document parsers."""
    
    @abstractmethod
    def parse(self, pdf_path: str) -> Dict[str, Any]:
        """Extract structured Markdown content and physical layout metadata from a PDF file.
        
        Args:
            pdf_path (str): File path to the bronze PDF document.
            
        Returns:
            Dict[str, Any]: Extracted metadata, physical page indices, and Markdown text.
        """
        pass


class BaseRetriever(ABC):
    """Abstract interface for legal information retrieval engines."""
    
    @abstractmethod
    def retrieve(self, query: str, t_event: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Retrieve relevant legal passages effective as of the specific target event date.
        
        Args:
            query (str): User legal query string.
            t_event (str): Target event date string in YYYY-MM-DD format.
            top_k (int): Number of top candidate documents to return.
            
        Returns:
            List[Dict[str, Any]]: Ranked list of matching legal document chunks with metadata.
        """
        pass