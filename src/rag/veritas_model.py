from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.core.config import SystemConfig
from src.core.interfaces import BaseRetriever
from src.rag.temporal_retriever import RetrievedChunk, TemporalRetriever


class VeritasModel(BaseRetriever):
    """Seminar-stage VERITAS model: date-aware retrieval only.

    This facade keeps the benchmark-facing model entry point separate from
    the temporal retrieval implementation. HART, slice retrieval, and answer
    generation can be composed here in later project stages.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        gold_path: Optional[str | Path] = None,
        supersession_rules: Optional[
            str | Path | Iterable[Dict[str, Any]]
        ] = None,
        retriever: Optional[TemporalRetriever] = None,
    ):
        super().__init__(config=config)
        self.temporal_retriever = retriever or TemporalRetriever(
            config=self.config,
            gold_path=gold_path,
            supersession_rules=supersession_rules,
        )

    def retrieve(
        self,
        query: str,
        t_event: Optional[str] = None,
        top_k: Optional[int] = None,
        date_basis: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        return self.temporal_retriever.retrieve(
            query=query,
            t_event=t_event,
            top_k=top_k,
            date_basis=date_basis,
        )
