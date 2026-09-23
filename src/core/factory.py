from importlib import import_module
from typing import Dict, Optional, Type

from src.core.config import SystemConfig
from src.core.interfaces import (
    BaseContractGate,
    BaseRetriever,
)


class ComponentFactory:

    _CONTRACT_GATES: Dict[
        str,
        str
    ] = {
        "document": (
            "src.contracts.document_contract"
            ":ExecutableDocumentContract"
        )
    }

    _RETRIEVERS: Dict[
        str,
        str
    ] = {
        "temporal": (
            "src.rag.temporal_retriever"
            ":TemporalRetriever"
        ),
        "bm25": (
            "src.baselines.bm25_baseline"
            ":BM25Baseline"
        ),
        "naive_rag": (
            "src.baselines.naive_rag_baseline"
            ":NaiveRAGBaseline"
        ),
    }

    @staticmethod
    def _load_class(
        target: str
    ):

        module_name, class_name = (
            target.split(":")
        )

        module = import_module(
            module_name
        )

        return getattr(
            module,
            class_name,
        )

    @classmethod
    def create_contract_gate(
        cls,
        config: Optional[
            SystemConfig
        ] = None,
        contract_type: str = "document",
    ) -> BaseContractGate:

        config = (
            config
            or SystemConfig()
        )

        target = cls._CONTRACT_GATES.get(
            contract_type
        )

        if target is None:
            raise ValueError(
                f"Unknown contract type: "
                f"{contract_type}"
            )

        contract_class = (
            cls._load_class(target)
        )

        return contract_class(
            config=config
        )

    @classmethod
    def create_retriever(
        cls,
        config: Optional[
            SystemConfig
        ] = None,
        retriever_type: str = "temporal",
    ) -> BaseRetriever:

        config = (
            config
            or SystemConfig()
        )

        target = cls._RETRIEVERS.get(
            retriever_type
        )

        if target is None:
            raise ValueError(
                f"Unknown retriever type: "
                f"{retriever_type}"
            )

        retriever_class = (
            cls._load_class(target)
        )

        return retriever_class(
            config=config
        )

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