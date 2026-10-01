from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv
from groq import Groq


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
        model_name: str = "openai/gpt-oss-120b",
        api_key: str | None = None,
    ):
        load_dotenv()

        api_key = api_key or os.getenv("GROQ_API_KEY")

        if not api_key:
            raise ValueError(
                "GROQ_API_KEY not found in .env or environment variables."
            )

        self.client = Groq(api_key=api_key)
        self.model_name = model_name

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
            temperature=0,
            max_tokens=512,
        )

        latency = (time.perf_counter() - start) * 1000

        answer = response.choices[0].message.content.strip()

        return SystemOutput(
            question=question,
            answer=answer,
            retrieved_chunks=chunks,
            latency_ms=latency,
        )


# ============================================================
# Example
# ============================================================

if __name__ == "__main__":

    chunks = [
        RetrievedChunk(
            chunk_id="demo_chunk",
            article_id="article_1",
            logical_doc_id="168/2024/NĐ-CP",
            text="Nghị định này có hiệu lực thi hành từ ngày 01/01/2025.",
            global_page=110,
            score=9.82,
        )
    ]

    agent = GeneratorAgent()

    result = agent.generate(
        question="Nghị định 168/2024 có hiệu lực từ ngày nào?",
        chunks=chunks,
    )

    print(result.answer)
    print(f"Latency: {result.latency_ms:.2f} ms")