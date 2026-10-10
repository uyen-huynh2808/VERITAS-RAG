from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv
from groq import Groq
from src.core.config import load_system_config


# ============================================================
# Shared data classes
# ============================================================

@dataclass
class RetrievedChunk:
    chunk_id: str
    article_id: str
    logical_doc_id: str

    text: str
    global_page: int
    score: float

    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    provision_id: Optional[str] = None
    provision_path: Optional[str] = None
    effectivity_mode: Optional[str] = None
    trigger_reference: Optional[str] = None


@dataclass
class SystemOutput:
    question: str
    answer: str
    retrieved_chunks: List[RetrievedChunk]
    latency_ms: float


# ============================================================
# Shared Groq Generator
# ============================================================

class GeneratorAgent:
    """
    Shared Generator used by every retrieval model.

    BM25 --------\
    Naive RAG -----> Generator -----> Answer
    VERITAS -----/

    The generator NEVER performs retrieval.
    """

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
    ):
        settings = load_system_config()
        load_dotenv()

        api_key = api_key or os.getenv("GROQ_API_KEY")

        if not api_key:
            raise ValueError(
                "GROQ_API_KEY not found in .env or environment variables."
            )

        self.client = Groq(api_key=api_key)
        self.model_name = model_name or settings.models.llm_model
        self.temperature = settings.models.generation_temperature
        self.max_tokens = settings.models.generation_max_tokens

    # --------------------------------------------------------
    # Prompt Builder
    # --------------------------------------------------------

    @staticmethod
    def build_prompt(
        question: str,
        chunks: List[RetrievedChunk],
    ) -> str:

        context_blocks = []

        for i, chunk in enumerate(chunks, start=1):

            context_blocks.append(
                f"""[Context {i}]
Document: {chunk.logical_doc_id}
Article: {chunk.article_id}
Provision: {chunk.provision_path or 'unknown'}
Page: {chunk.global_page}

{chunk.text}
"""
            )

        context = "\n".join(context_blocks)

        return f"""
Bạn là trợ lý hỏi đáp pháp luật Việt Nam.

Chỉ được trả lời dựa trên CONTEXT được cung cấp.
Không tự bổ sung kiến thức bên ngoài.
Nếu context không đủ, hãy trả lời đúng: "Không đủ thông tin trong tài liệu."

========================
CONTEXT
========================

{context}

========================
QUESTION
========================

{question}

========================
ANSWER
========================
"""

    # --------------------------------------------------------
    # Generation
    # --------------------------------------------------------

    def generate(
        self,
        question: str,
        chunks: List[RetrievedChunk],
    ) -> SystemOutput:

        prompt = self.build_prompt(question, chunks)

        start = time.perf_counter()

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Bạn là trợ lý QA pháp luật Việt Nam. "
                        "Chỉ sử dụng thông tin trong context."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        latency = (time.perf_counter() - start) * 1000

        answer = response.choices[0].message.content.strip()

        return SystemOutput(
            question=question,
            answer=answer,
            retrieved_chunks=chunks,
            latency_ms=latency,
        )


