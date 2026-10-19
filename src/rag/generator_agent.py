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
    issued_date: Optional[str] = None
    date_basis: Optional[str] = None
    is_in_force_at_event: Optional[bool] = None


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
        self.reasoning_effort = settings.models.generation_reasoning_effort
        self.include_reasoning = settings.models.generation_include_reasoning

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

            temporal_note = ""
            date_basis = getattr(chunk, "date_basis", None)
            if date_basis:
                in_force = getattr(chunk, "is_in_force_at_event", None)
                if in_force is True:
                    applicability = "in force at t_event"
                elif in_force is False:
                    applicability = "not in force at t_event"
                else:
                    applicability = "effectivity at t_event unknown"
                temporal_note = (
                    f"Issued: {getattr(chunk, 'issued_date', None) or 'unknown'}; "
                    f"effective: {chunk.effective_from or 'unknown'} to "
                    f"{chunk.effective_to or 'open-ended'}; "
                    f"status: {applicability}; retrieval basis: {date_basis}.\n"
                )

            context_blocks.append(
                f"""[Context {i}]
Document: {chunk.logical_doc_id}
Article: {chunk.article_id}
Provision: {chunk.provision_path or 'unknown'}
Page: {chunk.global_page}
{temporal_note}

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

Temporal rule: an issued date determines whether a document was available to describe. The effective interval determines whether its provisions were legally in force. If a context says it was not in force at t_event, do not present it as applicable law at that time.

Answer only with facts directly supported by the context. Keep the answer concise and name the relevant article or provision. End with a separate line in this exact format, replacing DOCUMENT_ID with the actual ID: Nguồn: DOCUMENT_ID. Use the exact Document ID from the context header and cite only the source that directly supports the answer. Do not use legal-document IDs mentioned only inside a chunk's body as source citations. Do not add background facts or legal conclusions from memory.

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
            reasoning_effort=self.reasoning_effort,
            include_reasoning=self.include_reasoning,
        )

        latency = (time.perf_counter() - start) * 1000

        answer = response.choices[0].message.content.strip()

        return SystemOutput(
            question=question,
            answer=answer,
            retrieved_chunks=chunks,
            latency_ms=latency,
        )


