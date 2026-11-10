
|Benchmark field|Đối chiếu với Gold Parquet|Cách dùng|
|---|---|---|
|`id`|**Không có**|ID của benchmark question, tự đặt `q001`|
|`query`|`text_content` + metadata của chunk|Đọc query → tìm chunk chứa evidence trả lời|
|`t_event`|`effective_from`, `issued_date` **và nội dung văn bản**|Là mốc thời gian để kiểm tra tính hợp lệ; **không phải** lấy `t_event` = `effective_from`|
|`difficulty`|Không có|Mày tự annotate|
|`category`|Không có|Mày tự annotate|
|`gold_doc_ids`|`logical_doc_id`|Document nào chứa evidence cần thiết|
|`gold_article_ids`|`article_id`|Article/Điều nào chứa evidence|
|`gold_chunk_ids`|`chunk_id`|Chunk cụ thể chứa evidence|
|`gold_answer`|`text_content` + văn bản gốc|Từ evidence viết đáp án chuẩn, **không copy nguyên chunk**|
|`gold_citation_doc_ids`|`logical_doc_id`|Document(s) mà answer cần cite|

---

|Temporal|Phạm vi|
|---|---|
|Intra-document|`issued_date`, `effective_from`, `effective_to`|
|Cross-document|Amendment / Replace relationship|

`document_contract.py` đang xử lý intra-document.

`temporal_relationship_validator.py` xử lý cross-document. File này là corpus-level validator để sau này dùng khi có nhiều văn bản và bảng quan hệ. Điều đó phù hợp với README vì README không bắt buộc Cross-document ở giai đoạn Gold.

---

Đổi default model từ:

```
model_name: str = "gemini-1.5-flash"
```

thành:

```
model_name: str = "openai/gpt-oss-120b"
```

---

1. `RetrievedChunk` thêm:
    - `effective_from`
    - `effective_to`
    
2. BM25 và Naive retriever khi tạo `RetrievedChunk` phải truyền thêm:

```
effective_from=row["effective_from"],
effective_to=row["effective_to"],
```

3. Faithfulness bây giờ là answer ↔ retrieved evidence (groundedness proxy), không còn answer ↔ gold_answer. Seminar dùng lexical proxy, khóa luận thay bằng HART.

---

**Fixed**
- Fixed benchmark ground-truth construction so `gold_article_ids` and
  `gold_chunk_ids` are taken from manual annotations instead of automatically
  assigning all articles/chunks belonging to a gold document.
- Fixed benchmark evaluation to use the configured `top_k` consistently for
  both retrieval and metric computation.
- Unified retriever interfaces with an optional `t_event` parameter; BM25
  and Naive Dense baselines intentionally ignore temporal filtering.

**Changed**
- Updated the default LLM configuration from Gemini to Groq
  (`openai/gpt-oss-120b`).
- Updated `SystemConfig` paths to match the current `data/{bronze,silver,gold}`
  directory structure.
- Corrected `ComponentFactory` retriever mappings to the actual retriever
  class names.

**Notes**
- Rebuilding Gold may regenerate article/chunk IDs because article IDs are
  currently generated from UUIDs. Existing benchmark annotations must
  therefore be validated/remapped after rebuilding the Gold dataset.
