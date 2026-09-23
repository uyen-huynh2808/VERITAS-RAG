import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclass
class EvidenceItem:
    """
    Structured evidence container preserving end-to-end provenance lineage.

    Evidence is expected to have already passed temporal filtering by the
    retriever before being provided to the generator.
    """

    chunk_id: str
    article_id: str
    logical_doc_id: str
    physical_file_id: str
    file_path: str
    part_no: int
    physical_page: int
    global_page: int
    article_number: str
    text_content: str
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    status: str = "active"
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_provenance_citation_tag(self) -> str:
        """
        Generate a standardized citation tag containing logical and
        physical provenance information.
        """
        return (
            f"[{self.logical_doc_id} - {self.article_number} | "
            f"Part {self.part_no}, Physical Page {self.physical_page} "
            f"(Global Page {self.global_page})]"
        )


@dataclass
class CitationReference:
    """
    Citation mapping from a generated citation index to the original
    evidence and its physical lineage.
    """

    citation_index: int
    chunk_id: str
    article_id: str
    logical_doc_id: str
    physical_file_id: str
    file_path: str
    part_no: int
    physical_page: int
    global_page: int
    cited_text_snippet: str


@dataclass
class GeneratedAnswer:
    """
    Output envelope containing the generated answer and citation mappings.

    Citation mappings are extracted from the draft answer. They are not
    considered entailment-verified until they pass the HART NLI gate.
    """

    query: str
    answer_text: str
    citations: List[CitationReference]
    used_evidences: List[EvidenceItem]
    model_name: str
    t_event: Optional[str] = None


class LegalGeneratorAgent:
    """
    Generator responsible for synthesizing a legal answer strictly from
    retrieved evidence.

    Temporal validity is handled upstream by the temporal retriever.
    Citation entailment is verified downstream by the HART NLI gate.
    """

    def __init__(
        self,
        llm_client: Any = None,
        model_name: str = "gemini-1.5-flash",
    ):
        self.llm_client = llm_client
        self.model_name = model_name

    def build_provenance_prompt(
        self,
        query: str,
        evidences: List[EvidenceItem],
        t_event: Optional[str] = None,
    ) -> str:
        """
        Construct a grounded prompt where each evidence item is explicitly
        labeled with its logical and physical provenance.
        """
        evidence_blocks: List[str] = []

        for idx, evidence in enumerate(evidences, start=1):
            citation_tag = evidence.to_provenance_citation_tag()

            validity_info = (
                f"Effective: {evidence.effective_from or 'N/A'} "
                f"to {evidence.effective_to or 'Present'}"
            )

            block = (
                f"--- EVIDENCE [{idx}] ---\n"
                f"Citation Label: {citation_tag}\n"
                f"Chunk ID: {evidence.chunk_id}\n"
                f"Article: {evidence.article_number} "
                f"({evidence.article_id})\n"
                f"Logical Document: {evidence.logical_doc_id}\n"
                f"Physical Provenance: "
                f"File={evidence.file_path} "
                f"(Hash: {evidence.physical_file_id[:8]}...), "
                f"Part={evidence.part_no}, "
                f"Physical Page={evidence.physical_page}, "
                f"Global Page={evidence.global_page}\n"
                f"Validity: {validity_info}\n"
                f"Content:\n{evidence.text_content}\n"
            )

            evidence_blocks.append(block)

        context_str = "\n".join(evidence_blocks)

        event_str = (
            f"Target Event Date (t_event): {t_event}\n"
            if t_event
            else ""
        )

        return f"""You are an expert Vietnamese Legal AI Assistant operating under the VERITAS-RAG framework.

{event_str}
Your task is to answer the user's query strictly based on the provided legal evidences below.

CRITICAL INSTRUCTIONS:
1. Every legal claim or statement you make MUST be directly supported by one or more provided evidences.
2. Cite supporting evidence using the exact citation format [EVIDENCE_INDEX], such as [1] or [2].
3. Do NOT introduce legal provisions, facts, dates, or source references that are not present in the provided evidences.
4. Do NOT fabricate or modify citation information.
5. Prefer precise and concise answers grounded in the provided evidence.

LEGAL EVIDENCES:
{context_str}

USER QUERY:
{query}

ANSWER:
"""

    def generate(
        self,
        query: str,
        evidences: List[EvidenceItem],
        t_event: Optional[str] = None,
    ) -> GeneratedAnswer:
        """
        Generate a grounded draft answer and extract citation-to-evidence
        mappings from the generated text.

        Citation entailment is verified downstream by HART.
        """
        if not evidences:
            logger.warning("No evidences provided for query: %s", query)

            return GeneratedAnswer(
                query=query,
                answer_text=(
                    "Không tìm thấy văn bản pháp luật phù hợp "
                    "để trả lời câu hỏi."
                ),
                citations=[],
                used_evidences=[],
                model_name=self.model_name,
                t_event=t_event,
            )

        prompt = self.build_provenance_prompt(
            query=query,
            evidences=evidences,
            t_event=t_event,
        )

        if self.llm_client is not None:
            raw_response = self.llm_client.generate(
                prompt=prompt,
                model=self.model_name,
            )

            if isinstance(raw_response, dict):
                answer_text = raw_response.get("text", "")
            else:
                answer_text = str(raw_response)

        else:
            # Deterministic fallback used for development/baseline testing.
            answer_text = self._mock_grounded_generation(
                query=query,
                evidences=evidences,
            )

        citations = self._extract_citations(
            answer_text=answer_text,
            evidences=evidences,
        )

        return GeneratedAnswer(
            query=query,
            answer_text=answer_text,
            citations=citations,
            used_evidences=evidences,
            model_name=self.model_name,
            t_event=t_event,
        )

    def _extract_citations(
        self,
        answer_text: str,
        evidences: List[EvidenceItem],
    ) -> List[CitationReference]:
        """
        Map numeric citation markers such as [1] and [2] in the generated
        answer back to their corresponding evidence items.

        This performs citation mapping only; it does not verify entailment.
        """
        matches = re.findall(r"\[(\d+)\]", answer_text)

        cited_indices = sorted(
            {int(match) for match in matches}
        )

        citations: List[CitationReference] = []

        for citation_index in cited_indices:
            if not (1 <= citation_index <= len(evidences)):
                logger.warning(
                    "Generated answer contains invalid citation index: %s",
                    citation_index,
                )
                continue

            evidence = evidences[citation_index - 1]

            snippet = evidence.text_content[:150]
            if len(evidence.text_content) > 150:
                snippet += "..."

            citations.append(
                CitationReference(
                    citation_index=citation_index,
                    chunk_id=evidence.chunk_id,
                    article_id=evidence.article_id,
                    logical_doc_id=evidence.logical_doc_id,
                    physical_file_id=evidence.physical_file_id,
                    file_path=evidence.file_path,
                    part_no=evidence.part_no,
                    physical_page=evidence.physical_page,
                    global_page=evidence.global_page,
                    cited_text_snippet=snippet,
                )
            )

        return citations

    def _mock_grounded_generation(
        self,
        query: str,
        evidences: List[EvidenceItem],
    ) -> str:
        """
        Deterministic grounded response used when no LLM client is configured.

        This is intended for development and baseline pipeline testing.
        """
        first_evidence = evidences[0]

        snippet = first_evidence.text_content[:200]
        if len(first_evidence.text_content) > 200:
            snippet += "..."

        return (
            f"Căn cứ theo quy định tại "
            f"{first_evidence.article_number} thuộc văn bản "
            f"{first_evidence.logical_doc_id} [1], "
            f"nội dung quy định như sau: {snippet}"
        )
