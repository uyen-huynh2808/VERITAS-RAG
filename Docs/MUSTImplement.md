
`ChunkBuilder` chưa đọc config

Hiện:

```
ChunkBuilder(
    chunk_size=512,
    chunk_overlap=64,
)
```

Trong khi `config.yaml` có:

```
chunking:
  max_tokens: 512
  overlap: 64
```

Đây là hard-code, nhưng vì giá trị giống nhau nên không ảnh hưởng seminar. 

---

`broken_cells_ratio` dùng Markdown

Hiện metric này phân tích:

```
content.splitlines()
```

Trong khi parser đã có structured `tables`.

Không sai, nhưng hơi lệch với ý tưởng “Silver normalized table”. Thesis có thể đổi sang dùng `tables`, seminar giữ nguyên vẫn hợp lý.

---

**WORDING**

1. **Thêm Experimental Protocol (thiếu nhất)**

Bổ sung ở đâu: Sau mục Evaluation hoặc trước Benchmark. Mục mới: `## Experimental Protocol`

Nội dung nên có:

- Seed = 42
- Cùng hardware
- Cùng dataset split
- Baseline và VERITAS chạy cùng cấu hình
- Báo cáo mean ± std (5 runs nếu có)

Mục đích: đáp ứng ý thầy “Baseline tái lập được và cùng protocol”.

2. Thêm Temporal QA Benchmark

Bổ sung ở đâu: Trong mục Dataset / Benchmark. Hiện chỉ nói benchmark pháp luật, nên thêm bảng minh hoạ:

|Question|As-of date|Expected law|
|---|---|---|
|Điều kiện nghỉ hưu?|2021-01-01|NĐ cũ|
|Điều kiện nghỉ hưu?|2024-01-01|NĐ mới|

Quan trọng là README phải nói rõ:

> `as_of_date` là đầu vào của truy vấn, không phải metadata của document.

3. Thêm Failure Taxonomy

Bổ sung ở đâu: Sau mục Mutation Suite. Thêm 2 nhóm lỗi:

|Data Error|Reasoning Error|
|---|---|
|OCR corruption|Wrong retrieval|
|Wrong effective date|Hallucination|
|Broken table|Unsupported claim|
|Duplicate article|Wrong citation|

Điều này đúng với nhận xét của thầy: “Tách OCR/data quality khỏi reasoning error.”

README hiện mới có mutation. Nên có đúng một hình kiểu này:

![](data:image/svg+xml;charset=utf-8,%3Csvg%20font-family%3D%22-apple-system-body%2C%20ui-sans-serif%2C%20-apple-system%2C%20system-ui%2C%20%26quot%3BSegoe%20UI%26quot%3B%2C%20Helvetica%2C%20%26quot%3BApple%20Color%20Emoji%26quot%3B%2C%20Arial%2C%20sans-serif%2C%20%26quot%3BSegoe%20UI%20Emoji%26quot%3B%2C%20%26quot%3BSegoe%20UI%20Symbol%26quot%3B%22%20font-weight%3D%22400%22%20data-d-component%3D%22svg%22%20fill%3D%22currentColor%22%20stroke%3D%22none%22%20style%3D%22color%3Argb\(13%2C%2013%2C%2013\)%22%20viewBox%3D%220%200%20320%20168%22%20width%3D%22100%25%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Crect%20width%3D%22320%22%20height%3D%22168%22%20rx%3D%2212%22%20fill%3D%22%23F8FAFC%22%2F%3E%3Crect%20x%3D%2278%22%20y%3D%2212%22%20width%3D%22164%22%20height%3D%2228%22%20rx%3D%228%22%20fill%3D%22%23D1D5DB%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%2230%22%20font-size%3D%2210%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%23111827%22%3EQA%20Failure%3C%2Ftext%3E%3Cpath%20d%3D%22M160%2040%20V54%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20stroke-linecap%3D%22round%22%2F%3E%3Cpath%20d%3D%22M160%2054%20L92%2068%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20stroke-linecap%3D%22round%22%2F%3E%3Cpath%20d%3D%22M160%2054%20L228%2068%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20stroke-linecap%3D%22round%22%2F%3E%3Crect%20x%3D%2220%22%20y%3D%2268%22%20width%3D%22124%22%20height%3D%2288%22%20rx%3D%2210%22%20fill%3D%22%23FEE2E2%22%2F%3E%3Ctext%20x%3D%2282%22%20y%3D%2284%22%20font-size%3D%2210%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20font-weight%3D%22bold%22%20fill%3D%22%23991B1B%22%3EData%20Error%3C%2Ftext%3E%3Ctext%20x%3D%2230%22%20y%3D%22102%22%20font-size%3D%228%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%237F1D1D%22%3E%E2%80%A2%20OCR%20corruption%3C%2Ftext%3E%3Ctext%20x%3D%2230%22%20y%3D%22116%22%20font-size%3D%228%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%237F1D1D%22%3E%E2%80%A2%20Wrong%20dates%3C%2Ftext%3E%3Ctext%20x%3D%2230%22%20y%3D%22130%22%20font-size%3D%228%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%237F1D1D%22%3E%E2%80%A2%20Broken%20tables%3C%2Ftext%3E%3Ctext%20x%3D%2230%22%20y%3D%22144%22%20font-size%3D%228%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%237F1D1D%22%3E%E2%80%A2%20Duplicate%20articles%3C%2Ftext%3E%3Crect%20x%3D%22176%22%20y%3D%2268%22%20width%3D%22124%22%20height%3D%2288%22%20rx%3D%2210%22%20fill%3D%22%23DBEAFE%22%2F%3E%3Ctext%20x%3D%22238%22%20y%3D%2284%22%20font-size%3D%2210%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20font-weight%3D%22bold%22%20fill%3D%22%231D4ED8%22%3EReasoning%20Error%3C%2Ftext%3E%3Ctext%20x%3D%22186%22%20y%3D%22102%22%20font-size%3D%228%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%231E3A8A%22%3E%E2%80%A2%20Wrong%20retrieval%3C%2Ftext%3E%3Ctext%20x%3D%22186%22%20y%3D%22116%22%20font-size%3D%228%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%231E3A8A%22%3E%E2%80%A2%20Hallucination%3C%2Ftext%3E%3Ctext%20x%3D%22186%22%20y%3D%22130%22%20font-size%3D%228%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%231E3A8A%22%3E%E2%80%A2%20Unsupported%20claim%3C%2Ftext%3E%3C%2Fsvg%3E)

Cái này rất quan trọng vì nó biến project thành research.

 4. Làm rõ đóng góp nghiên cứu (Novelty)

Bổ sung ở đâu: Phần Overview hoặc Research Contribution.

Không nên ghi:

> VERITAS invents a legal RAG.

Nên ghi dạng:

> VERITAS integrates executable temporal data contracts, Medallion Lakehouse, and end-to-end provenance for Vietnamese statutory QA.

Đây là đóng góp ở mức system, đúng hơn với implementation.

5. Thêm Ablation Design

Bổ sung ở đâu: Trong Evaluation. Thêm bảng:

|Variant|Removed|
|---|---|
|Full|—|
|w/o Schema|Schema Contract|
|w/o Temporal|Temporal Contract|
|w/o Quality|Quality Contract|
|w/o Provenance|Provenance Contract|

README hiện có file YAML nhưng nên mô tả luôn mục đích của từng ablation.

6. Chỉnh terminology một chút

Bổ sung ở đâu: Toàn README.

|Hiện tại|Nên đổi|
|---|---|
|guarantee factuality|improve factual consistency|
|guarantee legal correctness|enforce temporal validity|
|trusted answer|traceable answer|

Đây là cách viết an toàn hơn khi sau này viết paper.

---

**IMPLEMET**

1. Thiếu Observation Time

Thầy ghi rất rõ:

> observation time, publication time, effective-from/to

Hiện model của bạn chỉ có:

```
issued_date
effective_from
effective_to
```

Chưa có:

```
observation_time
publication_time
```

Điều này không phải lỗi code, nhưng README không nên nói đã hỗ trợ observation time.

Khóa luận sau này nên mở rộng thành:

![](data:image/svg+xml;charset=utf-8,%3Csvg%20font-family%3D%22-apple-system-body%2C%20ui-sans-serif%2C%20-apple-system%2C%20system-ui%2C%20%26quot%3BSegoe%20UI%26quot%3B%2C%20Helvetica%2C%20%26quot%3BApple%20Color%20Emoji%26quot%3B%2C%20Arial%2C%20sans-serif%2C%20%26quot%3BSegoe%20UI%20Emoji%26quot%3B%2C%20%26quot%3BSegoe%20UI%20Symbol%26quot%3B%22%20font-weight%3D%22400%22%20data-d-component%3D%22svg%22%20fill%3D%22currentColor%22%20stroke%3D%22none%22%20style%3D%22color%3Argb\(13%2C%2013%2C%2013\)%22%20viewBox%3D%220%200%20320%2096%22%20width%3D%22100%25%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Crect%20width%3D%22320%22%20height%3D%2296%22%20rx%3D%2212%22%20fill%3D%22%23F3F4F6%22%2F%3E%3Cline%20x1%3D%2224%22%20y1%3D%2248%22%20x2%3D%22296%22%20y2%3D%2248%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20stroke-linecap%3D%22round%22%2F%3E%3Ccircle%20cx%3D%2256%22%20cy%3D%2248%22%20r%3D%226%22%20fill%3D%22%2364748B%22%2F%3E%3Ccircle%20cx%3D%22132%22%20cy%3D%2248%22%20r%3D%226%22%20fill%3D%22%232563EB%22%2F%3E%3Ccircle%20cx%3D%22212%22%20cy%3D%2248%22%20r%3D%226%22%20fill%3D%22%2316A34A%22%2F%3E%3Ctext%20x%3D%2256%22%20y%3D%2230%22%20font-size%3D%228%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%23374151%22%3EIssued%3C%2Ftext%3E%3Ctext%20x%3D%22132%22%20y%3D%2264%22%20font-size%3D%228%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%231D4ED8%22%3EPublished%3C%2Ftext%3E%3Ctext%20x%3D%22212%22%20y%3D%2230%22%20font-size%3D%228%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%23047857%22%3EEffective%3C%2Ftext%3E%3Ctext%20x%3D%22160%22%20y%3D%2282%22%20font-size%3D%228%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%2C%20Helvetica%2C%20sans-serif%22%20fill%3D%22%236B7280%22%3EObservation%20time%20comes%20from%20the%20query%3C%2Ftext%3E%3C%2Fsvg%3E)

Observation không nằm trong document; nó đến từ query. Đó là chỗ temporal retrieval hoạt động.

2. Benchmark temporal QA (bắt buộc)

Cần tạo:

```
question
as_of_date
gold_doc_id
gold_article
answer
```

Khoảng 100–300 câu là hợp lý cho khóa luận.

3. Mutation Suite hoàn chỉnh (bắt buộc)

Thầy ghi rõ 5 loại mutation.

|Mutation|Hiện trạng|
|---|---|
|OCR corruption|🟡|
|Wrong date|🟡|
|Revoked law|❌|
|Table break|🟡|
|Duplicate article|🟡|

Bạn đã có `mutation_suite.py`, nhưng thesis nên sinh được các mutation này tự động.

4. Cross-document temporal graph (nên có)

Hiện validator đã có:

```
A amends B
A replaces B
```

Nhưng chưa có bước extract relation từ corpus. Thesis nên có bảng:

|source|relation|target|
|---|---|---|
|12/2023|amends|08/2020|

Đây là dữ liệu để temporal validator chạy ở mức corpus.

5. Evaluation framework (bắt buộc)

Cần chạy:

```
BM25
Naive RAG
VERITAS
VERITAS w/o Temporal
VERITAS w/o Provenance
...
```

Rồi xuất metric.

6. Failure analysis (nên có)

Sau evaluation cần tự động log:

```
Error Type
---------
OCR
Temporal
Retrieval
Hallucination
Lineage
```

Đây là phần thesis rất thích vì có phân tích định tính.

---

**Thiết kế bạn dự định hoàn toàn hợp lý khi tách thành hai luồng:**

1. **Luồng nạp dữ liệu:** user upload file → đưa vào `incoming` → chạy ingestion để cập nhật lakehouse và Gold.
2. **Luồng hỏi đáp:** user hỏi → lấy câu hỏi và `t_event` → gọi VERITAS retriever trên Gold hiện có → generator tạo câu trả lời, rồi có thể chạy HART và truy vết lineage.

Với file upload mới, nên xử lý theo kiểu **bổ sung vào dữ liệu hiện có**, không dựng lại toàn bộ Gold. Có thể lưu file cũ trong `incoming_past` để lưu trữ, nhưng để bảo toàn dữ liệu cũ thì điều cốt yếu là pipeline chỉ ingest tài liệu mới hoặc chưa ingest, và có cơ chế nhận diện trùng/idempotency. Di chuyển file cũ có thể làm thay đổi đường dẫn được lưu trong lineage, nên cần tính đến nếu `file_path` là một phần provenance.

ID của dữ liệu cũ **có thể giữ nguyên** nếu không ghi đè/xóa chúng và quy tắc sinh ID ổn định. Code hiện tạo `chunk_id` dựa trên document, provision/article, trang và vị trí chunk; nhưng trước khi hứa ID luôn bất biến, cần xác nhận cách sinh `provision_id` và hành vi upsert khi ingest tài liệu cùng `logical_doc_id`. Tài liệu mới hoặc phiên bản luật mới có thể cần ID riêng.

Còn `run_pipeline.py` có thể được dùng sau này làm **script chạy và đo toàn bộ E2E**: ingestion → truy vấn VERITAS → generator → validation/lineage. UI sản phẩm không cần gọi lại toàn bộ pipeline cho mỗi câu hỏi; nó chỉ cần gọi luồng hỏi đáp trên Gold đã có.

---

