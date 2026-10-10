# DỰ ÁN VERITAS-RAG

> **Khung Kiến trúc AI-Ready Lakehouse Bảo chứng Hiệu lực và Truy vết Phả hệ phục vụ Hỏi đáp Pháp luật Việt Nam thông qua Hợp đồng Dữ liệu Đa tầng dưới điều kiện Biến động Quy chuẩn Thời gian**

------------------------------------------------------------------------
## 0. ĐỊNH HƯỚNG NGHIÊN CỨU & TÍNH MỚI (NOVELTY & RESEARCH GAP)

### A. Định hướng Cốt lõi: Data Engineering + Legal RAG

VERITAS-RAG là một **AI-Ready Lakehouse Framework** cho hỏi đáp pháp luật Việt Nam, kết hợp **Medallion Lakehouse** và **Executable Data Contracts** nhằm đảm bảo chất lượng dữ liệu, hiệu lực thời gian và khả năng truy vết.

Hai đóng góp chính:

1.  **Temporal Validity Contract**: bảo chứng văn bản hợp lệ tại `t_event`.
2.  **End-to-End Page-Level Lineage**: truy vết từ câu trả lời về đúng PDF và trang gốc.

### B. Ba Mốc Thời Gian Chuẩn Hóa (Temporal Triad)

-   **issued_date (`T_issued`)**: ngày ban hành.
-   **effective_from / effective_to (`T_start`, `T_end`)**: khoảng hiệu lực.
-   **t_event**: thời điểm xảy ra vụ việc trong truy vấn.

`effective_from` is extracted from the effective-date text, with provision-level rules able to override the document default; dates then inherit through the Article → Clause → Point tree unless a narrower rule applies. The tree also preserves `effectivity_mode` and `trigger_reference` for externally triggered or unknown dates. `effective_to` is stored when the source provides it and may remain `NULL` when no end date is known. Temporal validity uses the half-open interval `[effective_from, effective_to)` when both bounds are available.
### C. Failure Taxonomy

| **Nhóm**    | **Mô tả**                                                  |
| ----------- | ---------------------------------------------------------- |
| **Group A** | Data Quality & OCR Errors (Schema, OCR, Metadata, Layout)  |
| **Group B** | Reasoning & Inference Errors (Hallucination, sai suy luận) |

------------------------------------------------------------------------
## 1. TÊN ĐỀ TÀI & RESEARCH STATEMENT

### A. Official Thesis Title

**English**

> VERITAS-RAG: A Validity-Guaranteed and Lineage-Traceable AI-Ready Lakehouse Framework for Vietnamese Legal Question Answering via Multi-Layered Data Contracts under Temporal Regulatory Dynamics

**Tiếng Việt**

> VERITAS-RAG: Khung Kiến trúc Lakehouse Sẵn sàng cho AI Bảo chứng Hiệu lực và Truy vết Phả hệ phục vụ Hỏi đáp Pháp luật Việt Nam thông qua Hợp đồng Dữ liệu Đa tầng dưới điều kiện Biến động Quy chuẩn Thời gian

### B. Research Questions

-   **RQ1**: Thiết kế Executable Data Contracts đa tầng cho Legal Ingestion.
-   **RQ2**: Two-Tier Temporal Retrieval giải quyết Staleness & Recency Bias.
-   **RQ3**: Lineage + HART + OpenTelemetry phục vụ Explainability.

------------------------------------------------------------------------
## 2. KIẾN TRÚC HỆ THỐNG E2E & TECH STACK

### A. E2E Closed-loop Architecture

``` text
RAW PDF
   │
   ▼
CHECK INPUT + COMPUTE SOURCE HASH
   │
   ▼
HYBRID PARSER + METADATA EXTRACTION
(Docling / PyMuPDF)
   │
   ▼
PRE-BRONZE CONTRACT GATE
(Schema + source document provenance)
   │
   ▼
BRONZE PERSISTENCE
   │
   ▼
BRONZE CONTRACT CHECKPOINT
(Schema + document provenance)
   │
   ▼
SILVER PERSISTENCE
(Pages + hierarchical provisions)
   │
   ▼
SILVER CONTRACT CHECKPOINT
(Temporal + quality)
   │
   ▼
CHUNK BUILDER (reads materialized Silver)
   │
   ▼
GOLD CONTRACT CHECKPOINT → GOLD PERSISTENCE
(Chunk provenance + final integrity)
   │
   ▼
AS-OF-DATE RETRIEVER
   │
   ▼
VERITAS RETRIEVAL
   │
   ▼
SHARED GENERATOR (Groq)
   │
   ▼
HART VALIDATION
   │
   ▼
SLiCE LINEAGE
   │
   ▼
STREAMLIT DASHBOARD
```

> **Lưu ý:** 4-layer Contract không phải một checkpoint duy nhất mà là cơ chế governance xuyên suốt Medallion lifecycle.

> **Thứ tự theo implementation hiện tại:** parser trích xuất metadata trước; pre-Bronze contract chạy sau parsing nhưng trước Bronze persistence. Bronze và Silver được persist trước checkpoint tương ứng. Nếu checkpoint fail, bước promote tiếp theo bị chặn, nhưng các bản ghi đã ghi ở tầng hiện tại vẫn còn trong lakehouse. Bronze checkpoint xác minh provenance bằng hash của file nguồn; Silver checkpoint đánh giá nội dung và bảng parser trả về, không đọc lại các bản ghi đã persist để kiểm chứng round-trip.

> Seminar version: `doc_version_hash` là SHA-256 của physical PDF duy nhất.

> Thesis extension: khi hỗ trợ multi-part, `doc_version_hash` sẽ được tính từ ordered physical PDF hashes.

```
RAW PDF
   │
   ▼
        PARSER + METADATA
   Docling / PyMuPDF
   text / tables / pages
              │
              ▼
┌─────────────────────────────┐
│ PRE-BRONZE CONTRACT GATE    │
│ Schema + source provenance  │
└─────────────┬───────────────┘
              │ PASS
              ▼
┌─────────────────────────────┐
│ BRONZE                      │
│ parsed but near-raw data    │
│                             │
│ Contract checkpoint:        │
│ - Schema                    │
│ - Document provenance       │
└─────────────┬───────────────┘
              ▼
       SILVER PERSISTENCE
              │
              ▼
┌─────────────────────────────┐
│ SILVER                      │
│ parsed pages + provisions   │
│ article / clause / point    │
│ temporal metadata           │
│                             │
│ Contract checkpoint:        │
│ - Temporal                  │
│ - Quality                   │
└─────────────┬───────────────┘
              ▼
  CHUNK BUILDER reads Silver
              │
              ▼
┌─────────────────────────────┐
│ GOLD                        │
│ retrieval-ready chunks      │
│ chunk provenance            │
│ chunk_hash                  │
│ retrieval metadata          │
│                             │
│ Contract checkpoint:        │
│ - Chunk Provenance          │
│ - Final integrity           │
└─────────────┬───────────────┘
              ▼
       AS-OF-DATE RETRIEVAL
```

### B. Tech Stack
| **Thành phần**    | **Công nghệ**          |
| ----------------- | ---------------------- |
| **Ingestion**     | Watchdog + Pydantic v2 |
| **Parser**        | Docling + PyMuPDF      |
| **Lakehouse**     | DuckDB + Parquet       |
| **Embedding**     | BGE-M3                 |
| **Vector**        | Chroma/Qdrant          |
| **Generator**     | Groq                   |
| **Verification**  | HART (mDeBERTa-v3)     |
| **Observability** | OpenTelemetry + SLiCE  |
| **UI**            | Streamlit              |

### C. Page-Level Lineage

`Citation → span_id → chunk_id → provision_id → article_id → doc_id → PDF page`

------------------------------------------------------------------------
## 3. SYNTHETIC MUTATION SUITE & DATASET

### Mutation Types

1.  OCR Corruption
2.  Wrong Date
3.  Revoked Law
4.  Table Break
5.  Duplicated Article
### Dataset Strategy

-   **Pipeline Demo:** 3 PDF
-   **Corpus:** Vietnamese Legal Datasets
-   **Benchmark:** VN-LegalTemporal-Eval (150--200 QA)

------------------------------------------------------------------------
## 4. BASELINE MODELS SPECIFICATION

> **Temporal parameter note:** `t_event` is passed through the shared retriever interface for benchmark consistency. BM25 and Naive Dense intentionally ignore `t_event` and perform no temporal filtering.
> Only VERITAS-RAG applies the As-of-Date constraint.

### Baseline 1: BM25

-   Lexical Retrieval
-   Không semantic
-   Không temporal
- Không As-of Filter
### Baseline 2: Naive Dense RAG

-   Dense Embedding
-   Flat Chunking
-   Không Contract
-   Không As-of Filter
### Proposed: VERITAS-RAG

-   Temporal SQL Filter
-   Hierarchical Chunking
-   Multi-layer Contracts
-   Page Lineage
-   HART Verification
### Benchmark Fairness

Ba hệ thống **dùng chung Generator**:

``` text
BM25 Retrieval
Naive Dense Retrieval
VERITAS Retrieval
        │
        ▼
 Shared Generator (Groq)
        │
        ▼
 Shared Evaluation
```

------------------------------------------------------------------------
## 5. THIẾT KẾ THỰC NGHIỆM & METRICS

### A. Target Metrics

| **Metric**                     | **Target** |
| ------------------------------ | ---------- |
| **Temporal Validity Accuracy** | $\ge 95\%$ |
| **Invalid Citation Rate**      | $\le 5\%$  |
| **Contract Detection Rate**    | $\ge 95\%$ |
| **Lineage Traceability**       | $\ge 98\%$ |
| **Retrieval Recall@K**         | $\ge 90\%$ |
| **Answer Faithfulness**        | $\ge 0.90$ |
| **Pipeline Overhead**          | $< 20\%$   |

| Metric                             | Seminar | Khóa luận | So sánh 3 model? | Script chính                           |
| ---------------------------------- | ------- | --------- | ---------------- | -------------------------------------- |
| **Temporal Validity Accuracy**     | v       | v         | v                | `run_benchmark.py`                     |
| **Invalid Citation Rate**          | v       | v         | v                | `run_benchmark.py`                     |
| **Contract Detection Rate**        | v       | v         |                  | `run_contract_eval.py`                 |
| **Lineage Traceability**           |         | v         |                  | `run_benchmark.py` / `run_eval.py`     |
| **Retrieval Recall@K**             | v       | v         | v                | `run_benchmark.py`                     |
| **Answer Faithfulness**            | v       | v         | v                | `run_benchmark.py`                     |
| **Pipeline Overhead**              | Partial | Full      |                  | `run_benchmark.py` + `run_pipeline.py` |
| **Individual Rule Detection Rate** | v       | v         |                  | `run_contract_eval.py`                 |
| **Ablation metrics**               |         | v         |                  | `run_ablation.py`                      |
### B. Dataset preparation, annotation và benchmark

Benchmark dùng hai file theo hai vai trò riêng:

- `data/benchmark/questions.json` là nguồn câu hỏi đầu vào.
- `data/splits/vn_legal_temporal_eval_split.json` là split được tạo từ nguồn câu hỏi và Gold hiện tại. Đây là file cần annotate và cũng là file `run_benchmark.py` đọc mặc định (`benchmark.output_path` trong `config/config.yaml`).

Chạy lại luồng tạo Bronze, Silver, Gold và benchmark split:

```powershell
python -m run.prepare_gold_dataset
```

Sau khi lệnh chạy thành công, annotate các trường gold trong file split đầu ra. Một mẫu có thể có dạng:

```json
{
  "id": "q001",
  "query": "...",
  "t_event": "YYYY-MM-DD",
  "gold_doc_ids": ["168/2024/NĐ-CP"],
  "gold_provisions": [
    {
      "logical_doc_id": "168/2024/NĐ-CP",
      "provision_path": "50.1.a",
      "level": "point"
    }
  ],
  "gold_chunk_ids": [],
  "gold_answer": "...",
  "gold_citation_doc_ids": ["168/2024/NĐ-CP"]
}
```

`gold_provisions` phải có `logical_doc_id` và ít nhất một trong `provision_id` hoặc `provision_path`; `level` giúp thể hiện cấp Điều (`article`), Khoản (`clause`) hoặc Điểm (`point`). `provision_id` nếu dùng phải được chép từ Silver/Gold; `provision_path` giúp đối chiếu theo đường dẫn như `50`, `50.1`, `50.1.a`. Có thể giữ `gold_chunk_ids` cho dữ liệu benchmark cũ; khi có `gold_provisions`, Recall@K được chấm theo định danh provision.

Chạy từng baseline sau khi đã annotate split:

```powershell
python -m run.run_benchmark --model bm25 --resume
python -m run.run_benchmark --model naive --resume
```

`--resume` chỉ tái sử dụng câu trả lời đã lưu trong `results/cache`; nếu chưa có cache hợp lệ, mỗi mẫu vẫn gọi generator Groq. Baseline `naive` cũng cần model embedding được cấu hình (`models.embedding_model`) tải được hoặc đã có đầy đủ trong cache máy local. Các đường dẫn, model, `top_k` và tham số retrieval được đọc từ `config/config.yaml`.

### C. Metric definitions and schema alignment

Recall@K so sánh provision gold với provision trong các chunk được retrieve. Temporal Validity chấm các provision có `effective_from` đã biết; Temporal Coverage báo tỷ lệ provision retrieve được có ngày bắt đầu hiệu lực đã biết, để dữ liệu chưa xác định hoặc phụ thuộc điều kiện bên ngoài không bị tính nhầm là sai hiệu lực. Invalid Citation Rate hiện so sánh số hiệu văn bản pháp luật ở cấp document. Faithfulness là lexical overlap giữa câu trả lời và nội dung evidence, không phải đánh giá suy luận ngữ nghĩa.

### D. Hai nhóm thực nghiệm Seminar
|**Benchmarking**|**Contract Testing**|
|---|---|
|**BM25 vs Naive vs VERITAS**|Mutation Suite|
|**Temporal QA**|Detection Rate|
|**Recall@K**|Individual Rule Rate|
|**Faithfulness**|Ablation|

------------------------------------------------------------------------

## 6. CẤU TRÚC DỰ ÁN

``` text
VERITAS-RAG/
├── config/
├── data/
│   ├── incoming/
│   ├── quarantine/
│   ├── bronze/
│   ├── silver/
│   ├── gold/
│   └── splits/
├── src/
│   ├── core/
│   ├── contracts/
│   ├── ingestion/
│   ├── database/
│   ├── rag/
│   │   ├── chunk_builder.py
│   │   ├── temporal_retriever.py
│   │   ├── veritas_model.py
│   │   └── generator_agent.py
│   ├── baselines/
│   ├── verification/
│   ├── observability/
│   ├── data/
│   └── ui/
├── run/
│   ├── prepare_gold_dataset.py
│   ├── run_pipeline.py
│   ├── run_benchmark.py
│   ├── run_contract_eval.py
│   ├── run_ablation.py
│   └── run_eval.py
├── requirements.txt
└── README.md
```

------------------------------------------------------------------------
## 7. DANH MỤC MÃ NGUỒN & TIMELINE

### A. Code Inventory

| **File**                  | **Vai trò**                    |
| ------------------------- | ------------------------------ |
| `prepare_gold_dataset.py` | Materialize Bronze→Silver→Gold |
| `veritas_model.py`        | Temporal Retrieval             |
| `bm25_baseline.py`        | Baseline 1                     |
| `naive_rag_baseline.py`   | Baseline 2                     |
| `generator_agent.py`      | Shared Generator               |
| `run_benchmark.py`        | Shared Benchmark Orchestrator  |
| `run_contract_eval.py`    | Mutation Evaluation            |
| `run_ablation.py`         | Contract Ablation              |
### B. Dataset Lifecycle

``` text
RAW
 ↓
Contract
 ↓
Bronze
 ↓
Contract
 ↓
Silver
 ↓
Chunk Builder
 ↓
Contract
 ↓
Gold
 ↓
Annotation
 ↓
Benchmark JSON
```

### C. Benchmark Orchestration

```
                         ┌─────────────────────────┐
                         │ prepare_gold_dataset.py │
                         │ RAW → Bronze → Silver   │
                         │      → Gold             │
                         └────────────┬────────────┘
                                      │
                         SAME Gold corpus / IDs
                                      │
             ┌────────────────────────┼───────────────────────┐
             │                        │                       │
             ▼                        ▼                       ▼
      veritas_model.py          bm25_model.py          naive_model.py
             │                        │                       │
             └────────────────────────┼───────────────────────┘
                                      │
                                      ▼
                            generator_agent.py
                                      │
                                      ▼
                           ┌────────────────────┐
                           │ run_benchmark.py   │
                           │                    │
                           │ shared generator   │
                           │ evaluation metrics │
                           └────────────────────┘
```


------------------------------------------------------------------------
## 8. Nguyên lý Kiến trúc

> **Medallion defines the data lifecycle.**

> **Executable Data Contracts govern the data as it moves through that lifecycle.**

Bronze, Silver và Gold không thay thế Contract; mỗi tầng có checkpoint riêng phù hợp với mức độ materialization của dữ liệu.
