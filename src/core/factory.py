from importlib import import_module
from typing import Callable, Dict, Optional, Type

from src.core.config import SystemConfig
from src.core.interfaces import (
    BaseContractGate,
    BaseRetriever,
)


class ComponentFactory:
    """
    Factory for creating system components.

    Components are resolved from registries instead of
    hard-coded if/elif chains.
    """

    _CONTRACT_GATES: Dict[
        str,
        str,
    ] = {
        "document": (
            "src.contracts.document_contract"
            ":ExecutableDocumentContract"
        ),
    }

    _RETRIEVERS: Dict[
        str,
        str,
    ] = {
        "bm25": (
            "src.baselines.bm25_baseline"
            ":BM25Baseline"
        ),
        "temporal": (
            "src.rag.temporal_retriever"
            ":TemporalRetriever"
        ),
    }

    @staticmethod
    def _load_class(
        class_path: str,
    ) -> Type:
        """
        Dynamically import a class using:

            package.module:ClassName
        """

        module_name, class_name = class_path.split(
            ":"
        )

        module = import_module(module_name)

        return getattr(module, class_name)

    @classmethod
    def register_retriever(
        cls,
        name: str,
        class_path: str,
    ) -> None:
        """
        Register a custom retriever implementation.
        """

        cls._RETRIEVERS[name] = class_path

    @classmethod
    def register_contract_gate(
        cls,
        name: str,
        class_path: str,
    ) -> None:
        """
        Register a custom contract gate implementation.
        """

        cls._CONTRACT_GATES[name] = class_path

    @classmethod
    def create_contract_gate(
        cls,
        config: Optional[SystemConfig] = None,
        contract_type: str = "document",
    ) -> BaseContractGate:

        config = config or SystemConfig()

        try:
            class_path = cls._CONTRACT_GATES[
                contract_type
            ]
        except KeyError as exc:
            raise ValueError(
                f"Unknown contract gate: {contract_type}. "
                f"Available: {list(cls._CONTRACT_GATES)}"
            ) from exc

        contract_class = cls._load_class(
            class_path
        )

        return contract_class(config=config)

    @classmethod
    def create_retriever(
        cls,
        config: Optional[SystemConfig] = None,
        retriever_type: str = "temporal",
    ) -> BaseRetriever:

        config = config or SystemConfig()

        try:
            class_path = cls._RETRIEVERS[
                retriever_type
            ]
        except KeyError as exc:
            raise ValueError(
                f"Unknown retriever: {retriever_type}. "
                f"Available: {list(cls._RETRIEVERS)}"
            ) from exc

        retriever_class = cls._load_class(
            class_path
        )

        return retriever_class(config=config)