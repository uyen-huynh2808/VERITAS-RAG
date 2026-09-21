from src.core.config import SystemConfig
from src.core.interfaces import BaseContractGate, BaseRetriever


class ComponentFactory:
    """Factory design pattern for dynamic component instantiation based on SystemConfig."""
    
    @staticmethod
    def create_contract_gate(config: SystemConfig) -> BaseContractGate:
        """Instantiate the primary Pydantic Data Contract Gate.
        
        Args:
            config (SystemConfig): Global system configuration object.
            
        Returns:
            BaseContractGate: Concrete instance of the data contract gate.
        """
        from src.contracts.document_contract import ExecutableDocumentContract
        return ExecutableDocumentContract(config=config)

    @staticmethod
    def create_retriever(config: SystemConfig, retriever_type: str = "temporal") -> BaseRetriever:
        """Instantiate a retriever engine by type.
        
        Args:
            config (SystemConfig): Global system configuration object.
            retriever_type (str): Retrieval algorithm identifier ('temporal' or 'bm25').
            
        Returns:
            BaseRetriever: Concrete retriever instance.
        """
        if retriever_type == "bm25":
            from src.baselines.bm25_baseline import BM25Baseline
            return BM25Baseline(config=config)
        elif retriever_type == "temporal":
            from src.rag.temporal_retriever import TemporalRetriever
            return TemporalRetriever(config=config)
        else:
            raise ValueError(f"Unknown retriever type specified: '{retriever_type}'")