from importlib import import_module
from typing import Dict, Optional, Type

from src.core.config import SystemConfig
from src.core.interfaces import (
    BaseContractGate,
    BaseRetriever,
)


class ComponentFactory:

    _CONTRACT_GATES: Dict[str, str] = {
        "document": (
            "src.contracts.document_contract"
            ":ExecutableDocumentContract"
        )
    }

    _RETRIEVERS: Dict[str, str] = {
        "temporal": (
            "src.rag.temporal_retriever"
            ":TemporalRetriever"
        ),
        "bm25": (
            "src.baselines.bm25_baseline"
            ":BM25Retriever"
        ),
        "naive": (
            "src.baselines.naive_rag_baseline"
            ":NaiveDenseRetriever"
        ),
    }

    @staticmethod
    def _load_class(target: str):
        module_name, class_name = target.split(":")

        module = import_module(module_name)

        return getattr(module, class_name)

    @classmethod
    def create_contract_gate(
        cls,
        config: Optional[SystemConfig] = None,
        contract_type: str = "document",
    ) -> BaseContractGate:

        config = config or SystemConfig()

        target = cls._CONTRACT_GATES.get(contract_type)

        if target is None:
            raise ValueError(
                f"Unknown contract type: {contract_type}"
            )

        contract_class = cls._load_class(target)

        return contract_class(config=config)

    @classmethod
    def create_retriever(
        cls,
        config: Optional[SystemConfig] = None,
        retriever_type: str = "temporal",
        gold_path: Optional[str] = None,
    ) -> BaseRetriever:

        config = config or SystemConfig()

        target = cls._RETRIEVERS.get(retriever_type)

        if target is None:
            raise ValueError(
                f"Unknown retriever type: {retriever_type}"
            )

        retriever_class = cls._load_class(target)

        if retriever_type in {"bm25", "naive"}:
            if gold_path is None:
                raise ValueError(
                    f"gold_path is required for "
                    f"{retriever_type} retriever"
                )

            return retriever_class(gold_path)

        return retriever_class(config=config)

    @classmethod
    def register_contract_gate(
        cls,
        name: str,
        target: str,
    ) -> None:

        cls._CONTRACT_GATES[name] = target

    @classmethod
    def register_retriever(
        cls,
        name: str,
        target: str,
    ) -> None:

        cls._RETRIEVERS[name] = target