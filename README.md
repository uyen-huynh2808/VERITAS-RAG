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

| # | Mutation | Variant (severity) | Lớp đích | Rule kỳ vọng |
|---|---|---|---|---|
| 1 | OCR Corruption | `diacritic_loss`, `noise_injection`, `mixed` × low/medium/high | Quality | `DIACRITIC_DENSITY_LOW`, `OCR_NOISE_EXCEEDED` |
| 2 | Wrong Date | `effective_before_issued`, `end_before_start`, `impossible_calendar_date`; probe `shifted_consistent` | Temporal / Schema | `INVALID_START_DATE`, `INVALID_END_DATE`, `SCHEMA_VIOLATION` |
| 3 | Revoked Law | `still_in_force`, `ends_after_replacement`, `replaced_by_older_document` | Temporal (cross-document) | `REVOKED_TARGET_STILL_EFFECTIVE`, `RELATION_*_ORDER_INVALID` |
| 4 | Table Break | `ragged_rows` × low/medium/high, `missing_header`, `span_overflow` | Quality | `TABLE_INTEGRITY_FAILED`, `TABLE_HEADER_MISSING`, `MERGED_CELL_ISSUE` |
| 5 | Duplicated Article | `same_number`, `renumbered_copy`; probe `partial_near_duplicate` | Quality (structure) + Gold integrity | `DUPLICATE_ARTICLE_NUMBER`, `DUPLICATE_PROVISION_ID`, `DUPLICATE_ARTICLE_CONTENT`, `DUPLICATE_CHUNK_ID`, `DUPLICATE_CHUNK_CONTENT` |
| + | Metadata Corruption (mở rộng) | `invalid_doc_id`, `missing_title`, `unsupported_doc_type`, `missing_effective_from` | Schema | `SCHEMA_VIOLATION` |
| + | Provenance Tamper (mở rộng) | `source_file_modified`, `chunk_text_tampered`, `chunk_wrong_version`, `orphan_chunk` | Provenance | `DOCUMENT_HASH_MISMATCH`, `CHUNK_HASH_MISMATCH`, `CHUNK_DOCUMENT_HASH_MISMATCH`, `PROVISION_ID_MISSING` |
| 0 | Clean controls | `original`, `whitespace_reflow`, `valid_table`, `valid_replacement` | — | kỳ vọng PASS (đo false positive) |

Mutation được áp lên **bản sao** của bundle sạch (metadata + nội dung Silver + bảng + cây provision + chunk Gold) đọc từ lakehouse; PDF gốc và lakehouse không bị sửa. `source_file_modified` ghi vào một bản sao PDF trong thư mục tạm. Các *probe* (`in_scope: false`) là lỗi thật mà bộ rule hiện tại **không được thiết kế** để bắt; chúng được báo cáo riêng (`blind_spot_detection_rate`) thay vì trộn vào Detection Rate. Revoked Law dùng một văn bản tiền nhiệm giả lập (`DOC-<hex>`) vì corpus hiện chưa có cặp văn bản cũ/mới.
### Dataset Strategy

-   **Pipeline Demo:** 3 PDF
-   **Corpus:** Vietnamese Legal Datasets
-   **Benchmark:** VN-LegalTemporal-Eval (150--200 QA)

------------------------------------------------------------------------
## 4. BASELINE MODELS SPECIFICATION

> **Temporal parameter note:** `t_event` is passed through the shared retriever interface for benchmark consistency. BM25 and Naive Dense intentionally ignore `t_event` and perform no temporal filtering.
> Only VERITAS-RAG applies the As-of-Date constraint.

VERITAS distinguishes document availability from legal applicability. `date_basis: "issued"` filters by `issued_date` for questions about a law's contents; `date_basis: "effective"` filters by `[effective_from, effective_to)` for questions about which rule governed an event; `date_basis: "status"` retrieves issued text so the system can determine whether it had taken effect by `t_event`. In the benchmark, `document_understanding` and `amendment_retrieval` default to `issued`, direct effectivity-status questions use `status`, and other categories default to `effective`. A sample-level `date_basis` can override this.

Recall excludes samples without provision-level or chunk-level gold labels. Invalid Citation Rate uses `gold_citation_doc_ids`, falling back to document IDs in `gold_provisions` and then `gold_doc_ids`; samples without any of these labels are excluded. It extracts distinct legal document IDs only from explicit `Nguồn:`, `Nguon:`, `Source:`, `Sources:`, or `Citation:` lines; document numbers mentioned elsewhere in answer prose are not citations. Non-empty answers with no extracted citation are excluded from Invalid Citation Rate (N/A), while Citation Coverage records them as uncovered. Empty answers are N/A for both metrics.

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

-   Temporal filtering over Gold Parquet (pandas)
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
| **False Positive Rate (clean controls)** | v | v | | `run_mutation_suite.py` + `run_contract_eval.py` |
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
python -m run.run_benchmark --model veritas --resume
```

`--resume` chỉ tái sử dụng câu trả lời đã lưu trong `results/cache`; nếu chưa có cache hợp lệ, mỗi mẫu vẫn gọi generator Groq. Baseline `naive` cũng cần model embedding được cấu hình (`models.embedding_model`) tải được hoặc đã có đầy đủ trong cache máy local. Các đường dẫn, model, `top_k` và tham số retrieval được đọc từ `config/config.yaml`.

### C. Metric definitions and schema alignment

Recall@K so sánh provision gold với provision trong top-K chunk được retrieve; mỗi câu hỏi được chấm riêng rồi lấy macro-average trên các mẫu có gold label. Temporal Validity Accuracy là tỷ lệ chunk top-K có `effective_from` đã biết và khoảng `[effective_from, effective_to)` chứa `t_event`; `effective_to = NULL` được xem là chưa có cận trên được mã hóa. Mẫu `date_basis` là `issued` hoặc `status` không được chấm metric này. Đây là phép đánh giá metadata hiệu lực đang lưu trong Gold, không phải kiểm chứng độc lập tính đúng của metadata hay quan hệ sửa đổi/thay thế chưa được mã hóa. VERITAS lọc theo cùng metadata trước khi xếp hạng, nên cần diễn giải điểm cùng Temporal Coverage và số mẫu assessable, không dùng riêng nó làm bằng chứng độc lập rằng bộ lọc temporal đúng.

Invalid Citation Rate được tính trên các document ID citation phân biệt: số ID không thuộc gold chia cho tổng ID citation được trích. Câu trả lời không có citation được loại khỏi metric này (N/A), còn Citation Coverage ghi nhận riêng việc thiếu citation. Faithfulness trong benchmark seminar là lexical evidence-overlap proxy: số token phân biệt trong câu trả lời xuất hiện trong evidence chia cho số token phân biệt trong câu trả lời. Chỉ số này không đánh giá entailment, mâu thuẫn hay độ đúng ngữ nghĩa; không nên diễn giải nó như xác suất câu trả lời faithful.

### C2. Contract Testing: Mutation Suite, Detection Rate, Ablation

**Kiểm thử trên PDF lỗi thật (`Docs/Task1`)** — không cần lakehouse, không ghi gì vào `data/`:

```powershell
python -m run.run_contract_eval                       # Docs/Task1 + Docs/Task1/expected_results.json
python -m run.run_contract_eval --parser pymupdf      # ép parser (config | docling | pymupdf)
python -m run.run_contract_eval --disable-layers temporal   # ablation trên bộ PDF
```

Chạy theo hai lượt. Lượt 1 parse mọi PDF trong thư mục để lập danh mục văn bản (số hiệu, ngày ban hành, `effective_from`/`effective_to`, quan hệ sửa đổi/thay thế/bãi bỏ ghi trong văn bản). Lượt 2 cho từng PDF đi đúng đường của `prepare_gold_dataset.py` trước khi persist (hash nguồn → `PDFParser` → metadata → provisions → `ChunkBuilder.build_chunks`) rồi qua 4 checkpoint; rule giữa các văn bản dùng ngày thật của các PDF khác trong thư mục thay vì chỉ ngày được trích dẫn (`--no-corpus` để kiểm từng file riêng lẻ). Kết quả so với `expected_results.json` và ghi `results/contract/pdf_contract_eval_Task1.json`. `actual`/`stopped_at` là hành vi thật của pipeline (checkpoint đầu tiên chặn); `checkpoint_codes` gom mã lỗi của cả 4 checkpoint; `diagnostic_codes` là rule Temporal/Quality được thử trực tiếp khi Schema đã chặn trước (các lớp sau `NOT_CHECKED`); `PROBE` là ca đo độ nhạy (`in_scope: false`), không tính vào Detection Rate.

**Bộ kiểm thử `Docs/Task1` (20 PDF).** File 111, 444–999 làm tay; các file còn lại sinh bằng `python Docs/Task1/tao_bo_kiem_thu.py` (DOCX → PDF qua LibreOffice/Word, tự cập nhật manifest).

| File | Loại | Lỗi cài sẵn | Kỳ vọng | Rule |
|---|---|---|---|---|
| `111/2026` | sạch | — | PASS | — |
| `121/2026` | Quality | Toàn văn mất dấu tiếng Việt | chặn ở silver | `DIACRITIC_DENSITY_LOW` |
| `131/2026` | Schema | Số hiệu thiếu `-CP` | chặn ở pre_parse | `SCHEMA_VIOLATION` |
| `141/2025` | Temporal (giữa văn bản) | Bị 151 thay thế nhưng không có `effective_to` | chặn ở silver | `REVOKED_TARGET_STILL_EFFECTIVE` |
| `151/2026` | sạch | Văn bản thay thế 141 (lỗi nằm ở 141) | PASS | — |
| `161/2025` | sạch | Hiệu lực "đến hết ngày 31/3/2026", đúng ngày 171 thay thế | PASS | — |
| `171/2026` | sạch | Thay thế 161 hợp lệ | PASS | — |
| `181/2025` | Temporal (giữa văn bản) | Sửa đổi 141 nhưng có hiệu lực trước 141 | chặn ở silver | `RELATION_EFFECTIVE_ORDER_INVALID` |
| `191/2026` | Temporal | Hết hiệu lực trước khi có hiệu lực | chặn ở silver | `INVALID_END_DATE` |
| `211/2026` | sạch | Bảng hợp lệ + "Nghị định này hết hiệu lực kể từ ngày…" | PASS | — |
| `222/2026` | probe | Nhiễu OCR 1% (dưới ngưỡng) | PROBE (lọt) | — |
| `231/2026` | sạch | Sửa đổi 111 hợp lệ, trích “Điều 2. …” trong ngoặc kép | PASS | — |
| `241/2026` | sạch | Nhiều trang, có Khoản/Điểm, câu khuôn mẫu lặp | PASS | — |
| `333/2026` | Quality | Nhiễu OCR 15%, metadata còn nguyên | chặn ở silver | `OCR_TOKEN_CORRUPTION` |
| `444/2026` | Quality | Nhiễu OCR làm hỏng câu hiệu lực | chặn ở pre_parse | `SCHEMA_VIOLATION`; chẩn đoán `OCR_TOKEN_CORRUPTION` |
| `555/2026` | Temporal (giữa văn bản) | Sửa đổi NĐ 168/2026 ban hành *sau* chính nó | chặn ở silver | `RELATION_ISSUED_ORDER_INVALID` |
| `666/2026` | Quality | Điều 1 lặp lại | chặn ở silver | `DUPLICATE_ARTICLE_HEADING` |
| `777/2026` | Quality | Bảng vỡ ô, cột Năm sai | chặn ở silver | PyMuPDF: `TABLE_CELL_INTEGRITY_FAILED` + `TABLE_VALUE_INVALID`; Docling: `TABLE_VALUE_INVALID` |
| `888/2026` | Quality | Nhiễu OCR nặng (số hiệu, ngày hỏng) | chặn ở pre_parse | `SCHEMA_VIOLATION`; chẩn đoán `OCR_TOKEN_CORRUPTION` |
| `999/2026` | Temporal | Hiệu lực trước ngày ban hành | chặn ở silver | `INVALID_START_DATE` |

Kết quả (PyMuPDF, có tra chéo): 12 lỗi + 7 văn bản sạch + 1 probe; Contract Detection Rate 1,000; False Positive Rate 0,000; checkpoint chặn đúng kỳ vọng 1,000. Ablation trên bộ PDF: tắt Schema → lọt 131, 444, 888 (0,750); tắt Temporal → lọt 141, 181, 191, 555, 999 (0,583); tắt Quality → lọt 121, 333, 666, 777 (0,667); tắt Provenance → không lọt (1,000; lỗi provenance không tạo được bằng nội dung PDF).

Rule **không** kiểm được bằng PDF và được kiểm bằng mutation suite tổng hợp: toàn bộ lớp Provenance (hash nguồn, hash chunk, liên kết provision, trùng `chunk_id`), `DUPLICATE_ARTICLE_NUMBER`/`DUPLICATE_PROVISION_ID`/`DUPLICATE_ARTICLE_CONTENT` (parser bỏ heading lặp trước khi các rule này chạy), `TABLE_INTEGRITY_FAILED`/`TABLE_HEADER_MISSING`/`MERGED_CELL_ISSUE` (phụ thuộc cách parser trả bảng), `NULL_RATIO_EXCEEDED`. Kết quả phụ thuộc parser: luôn báo cáo kèm `parser_used` (Docling tự chuyển sang PyMuPDF khi không tải được model).

**Mutation suite tổng hợp** — sau khi đã chạy `python -m run.prepare_gold_dataset` (lakehouse có dữ liệu Gold):

```powershell
python -m run.run_mutation_suite
python -m run.run_contract_eval --input results/contract/mutation_results.json
python -m run.run_ablation
```

- `run_mutation_suite.py` đọc từng văn bản đã materialize đến Gold, sinh mutation (seed cố định từ `config.yaml`), cho mỗi case chạy lại đủ 4 checkpoint `pre_parse → bronze → silver → gold` và ghi `results/contract/mutation_results.json`. `stopped_at` là checkpoint đầu tiên chặn văn bản (ngữ nghĩa pipeline); `issue_codes` gom mã lỗi của mọi checkpoint để quy trách nhiệm cho từng rule.
- `run_contract_eval.py` tính Contract Detection Rate (chỉ mutation in-scope), Individual Rule Detection Rate (rule kỳ vọng có thực sự bắn hay không), detection theo lớp/severity/variant, False Positive Rate trên clean controls, blind-spot rate và confusion matrix → `results/contract/contract_metrics.json`.
- `run_ablation.py` chạy cùng tập case với contract đầy đủ và với từng cấu hình `config/ablations/wo_*.yaml` (tắt một lớp qua `disabled_layers`) → `results/contract/ablation_metrics.json` kèm Δ so với full.
- Smoke test không cần dữ liệu thật: thêm `--synthetic` (văn bản giả lập `DOC-<hex>`, **không dùng để báo cáo**).

Rule mới bổ sung trong `config/contract_rules.json` (đều có thể tắt):

- Quality / OCR: `corrupted_token_ratio` — tỷ lệ token bị thay ký tự kiểu OCR (`2o26`, `Đ0c`, `C#Ủ`, `d@`), ngưỡng `max_corrupted_token_ratio` 0,02 (`OCR_TOKEN_CORRUPTION`). `ocr_noise_ratio` không còn đếm ký tự định dạng vô hình (zero-width space…) và dấu `…`.
- Quality / bảng: `empty_cells_ratio` — tỷ lệ ô trống trong lưới bảng parser trả về, ngưỡng `max_empty_cells_ratio` 0,10 (`TABLE_CELL_INTEGRITY_FAILED`).
- Quality / giá trị bảng: số trong cột có tiêu đề `Năm` phải là năm 4 chữ số (`TABLE_VALUE_INVALID`). Docling dựng lại bảng vỡ thành lưới đúng hàng nên không còn ô trống; giá trị sai là dấu vết còn lại.
- Các khối trình bày mà parser có thể nhận nhầm là bảng (quốc hiệu, "Nơi nhận / TM. CHÍNH PHỦ", chữ ký) được loại khỏi mọi metric bảng, tránh chặn nhầm văn bản sạch.
- Quality / structure: heading `Điều N.` đầu dòng xuất hiện hai lần trong nội dung Silver (`DUPLICATE_ARTICLE_HEADING`) — parser chỉ giữ heading đầu tiên nên lỗi này không thấy được trong cây provision.
- Parser: chuẩn hóa ký tự trắng đặc biệt trước khi trích metadata/Điều (dấu cách không ngắt, gạch nối mềm, U+037E); trích `effective_to` khi văn bản tự nêu thời hạn ("có hiệu lực … đến (hết) ngày …", "Nghị định này hết hiệu lực kể từ ngày …").
- Cross-document: rule thứ tự ngày gắn lỗi cho văn bản nguồn (văn bản tự nhận sửa đổi/thay thế); `REVOKED_TARGET_STILL_EFFECTIVE` gắn lỗi cho văn bản đích (văn bản bị thay thế mà chưa đóng hiệu lực). Văn bản được trích dẫn mà không rõ ngày thì bỏ qua, không phải lỗi.
- `min_duplicate_chunk_tokens` = 40: câu khuôn mẫu 20–30 từ lặp ở nhiều Điều là bình thường trong văn bản luật.
- `src/ingestion/relation_extractor.py`: trích quan hệ `amends`/`replaces`/`repeals` từ chính văn bản ("sửa đổi, bổ sung … Nghị định số X ngày …", "thay thế …", "bãi bỏ …", "X hết hiệu lực kể từ …"); văn bản được trích dẫn chỉ cần số hiệu + ngày, rule cross-document chỉ so các mốc ngày đã biết.

- Temporal cross-document: quan hệ `amends`/`replaces`/`repeals` phải có văn bản nguồn ban hành và có hiệu lực không sớm hơn văn bản đích; với `replaces`/`repeals`, văn bản đích phải có `effective_to` ≤ `effective_from` của văn bản nguồn (`REVOKED_TARGET_STILL_EFFECTIVE`). Chỉ chạy khi truyền `relations` vào `validate_silver`.
- Quality / structure integrity: trùng số Điều, trùng `provision_id`, trùng toàn văn Điều (≥ 20 token). Chỉ chạy khi truyền `provisions` vào `validate_silver`; `prepare_gold_dataset.py` hiện **chưa** truyền, nên pipeline ingestion giữ nguyên hành vi cho đến khi False Positive Rate trên corpus thật được kiểm chứng.
- Provenance / Gold final integrity: trùng `chunk_id`, trùng nội dung chunk giữa các provision khác nhau (≥ 20 token). Rule này chạy trong `validate_gold` của pipeline.

`ExecutableDocumentContract(config, disabled_layers=[...], write_quarantine_reports=False)` dùng cho ablation/mutation; pipeline production dùng mặc định (đủ 4 lớp, ghi quarantine report).

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
│   ├── data/            # mutation_suite.py
│   └── ui/
├── run/
│   ├── prepare_gold_dataset.py
│   ├── run_mutation_suite.py
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
| `mutation_suite.py`       | Sinh mutation + replay checkpoint |
| `run_mutation_suite.py`   | Chạy Mutation Suite            |
| `relation_extractor.py`   | Trích quan hệ sửa đổi/thay thế/bãi bỏ |
| `run_contract_eval.py`    | Kiểm thử contract trên PDF lỗi + chấm điểm mutation |
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
