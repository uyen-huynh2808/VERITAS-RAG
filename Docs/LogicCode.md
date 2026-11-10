
Flow theo implementation hiện tại
![](data:image/svg+xml;charset=utf-8,%3Csvg%20font-family%3D%22-apple-system-body%2C%20ui-sans-serif%2C%20-apple-system%2C%20system-ui%2C%20%26quot%3BSegoe%20UI%26quot%3B%2C%20Helvetica%2C%20%26quot%3BApple%20Color%20Emoji%26quot%3B%2C%20Arial%2C%20sans-serif%2C%20%26quot%3BSegoe%20UI%20Emoji%26quot%3B%2C%20%26quot%3BSegoe%20UI%20Symbol%26quot%3B%22%20font-weight%3D%22400%22%20data-d-component%3D%22svg%22%20fill%3D%22currentColor%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%20style%3D%22color%3Argb\(13%2C%2013%2C%2013\)%22%20viewBox%3D%220%200%20320%20420%22%20width%3D%22100%25%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cdefs%3E%3Cmarker%20id%3D%22arrow%22%20markerWidth%3D%2210%22%20markerHeight%3D%2210%22%20refX%3D%225%22%20refY%3D%225%22%20orient%3D%22auto-start-reverse%22%3E%3Cpath%20d%3D%22M%200%200%20L%2010%205%20L%200%2010%20z%22%20fill%3D%22%239CA3AF%22%2F%3E%3C%2Fmarker%3E%3C%2Fdefs%3E%3Crect%20x%3D%2278%22%20y%3D%2218%22%20width%3D%22164%22%20height%3D%2234%22%20rx%3D%2210%22%20fill%3D%22%23F3F4F6%22%20stroke%3D%22%23D1D5DB%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%2239%22%20font-size%3D%2211%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%23111827%22%3EIncoming%20PDF%3C%2Ftext%3E%3Cline%20x1%3D%22160%22%20y1%3D%2252%22%20x2%3D%22160%22%20y2%3D%2268%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arrow\)%22%2F%3E%3Crect%20x%3D%2258%22%20y%3D%2268%22%20width%3D%22204%22%20height%3D%2242%22%20rx%3D%2210%22%20fill%3D%22%23DBEAFE%22%20stroke%3D%22%232563EB%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%2284%22%20font-size%3D%2211%22%20font-weight%3D%22bold%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%231D4ED8%22%3EPre-parse%20Contract%3C%2Ftext%3E%3Ctext%20x%3D%22160%22%20y%3D%2296%22%20font-size%3D%228%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%231D4ED8%22%3ESchema%20%2B%20Document%20Provenance%3C%2Ftext%3E%3Cline%20x1%3D%22160%22%20y1%3D%22110%22%20x2%3D%22160%22%20y2%3D%22126%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arrow\)%22%2F%3E%3Crect%20x%3D%2292%22%20y%3D%22126%22%20width%3D%22136%22%20height%3D%2234%22%20rx%3D%2210%22%20fill%3D%22%23F3F4F6%22%20stroke%3D%22%23D1D5DB%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%22147%22%20font-size%3D%2211%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%23111827%22%3EPDF%20Parser%3C%2Ftext%3E%3Cline%20x1%3D%22160%22%20y1%3D%22160%22%20x2%3D%22160%22%20y2%3D%22176%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arrow\)%22%2F%3E%3Crect%20x%3D%22104%22%20y%3D%22176%22%20width%3D%22112%22%20height%3D%2234%22%20rx%3D%2210%22%20fill%3D%22%23FDE68A%22%20stroke%3D%22%23D97706%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%22197%22%20font-size%3D%2211%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%2392400E%22%3EBronze%3C%2Ftext%3E%3Cline%20x1%3D%22160%22%20y1%3D%22210%22%20x2%3D%22160%22%20y2%3D%22226%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arrow\)%22%2F%3E%3Crect%20x%3D%2258%22%20y%3D%22226%22%20width%3D%22204%22%20height%3D%2242%22%20rx%3D%2210%22%20fill%3D%22%23DBEAFE%22%20stroke%3D%22%232563EB%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%22242%22%20font-size%3D%2211%22%20font-weight%3D%22bold%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%231D4ED8%22%3EBronze%20Checkpoint%3C%2Ftext%3E%3Ctext%20x%3D%22160%22%20y%3D%22254%22%20font-size%3D%228%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%231D4ED8%22%3ESchema%20%2B%20Provenance%3C%2Ftext%3E%3Cline%20x1%3D%22160%22%20y1%3D%22268%22%20x2%3D%22160%22%20y2%3D%22284%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arrow\)%22%2F%3E%3Crect%20x%3D%2292%22%20y%3D%22284%22%20width%3D%22136%22%20height%3D%2234%22%20rx%3D%2210%22%20fill%3D%22%23D1FAE5%22%20stroke%3D%22%23059669%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%22305%22%20font-size%3D%2211%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%23065F46%22%3ESilver%3C%2Ftext%3E%3Cline%20x1%3D%22160%22%20y1%3D%22318%22%20x2%3D%22160%22%20y2%3D%22334%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arrow\)%22%2F%3E%3Crect%20x%3D%2258%22%20y%3D%22334%22%20width%3D%22204%22%20height%3D%2242%22%20rx%3D%2210%22%20fill%3D%22%23DBEAFE%22%20stroke%3D%22%232563EB%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%22350%22%20font-size%3D%2211%22%20font-weight%3D%22bold%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%231D4ED8%22%3ESilver%20Checkpoint%3C%2Ftext%3E%3Ctext%20x%3D%22160%22%20y%3D%22362%22%20font-size%3D%228%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%231D4ED8%22%3ETemporal%20%2B%20Quality%3C%2Ftext%3E%3Cline%20x1%3D%22160%22%20y1%3D%22376%22%20x2%3D%22160%22%20y2%3D%22392%22%20stroke%3D%22%239CA3AF%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arrow\)%22%2F%3E%3Crect%20x%3D%22104%22%20y%3D%22392%22%20width%3D%22112%22%20height%3D%2222%22%20rx%3D%228%22%20fill%3D%22%23F3E8FF%22%20stroke%3D%22%237C3AED%22%2F%3E%3Ctext%20x%3D%22160%22%20y%3D%22407%22%20font-size%3D%2210%22%20text-anchor%3D%22middle%22%20font-family%3D%22Arial%22%20fill%3D%22%235B21B6%22%3EGold%3C%2Ftext%3E%3C%2Fsvg%3E)


| Bước                              | Code                                                   |
| --------------------------------- | ------------------------------------------------------ |
| Kiểm tra input + tính source hash | `process_document()`                                   |
| Parse PDF + trích metadata        | `PDFParser.parse()`                                    |
| Contract trước Bronze             | `validate_pre_parse()`                                 |
| Persist Bronze                    | `insert_bronze_parts()`                                |
| Bronze checkpoint                 | `validate_bronze()`                                    |
| Persist Silver pages/provisions   | `insert_silver_pages()` + `insert_silver_provisions()` |
| Silver checkpoint                 | `validate_silver()`                                    |
| Đọc Silver làm đầu vào chunking   | `_get_silver_provisions()` → `build_chunks()`          |
| Gold checkpoint rồi persist       | `validate_gold()` → `persist_chunks()`                 |

`validate_pre_parse()` chạy sau khi parser trích metadata, nhưng trước Bronze persistence. Bronze và Silver được ghi trước checkpoint tương ứng. Nếu checkpoint fail, bước tiếp theo bị chặn; bản ghi đã ghi ở tầng hiện tại vẫn còn trong lakehouse.

Bronze checkpoint kiểm tra schema và hash của file nguồn qua `storage_path`; nó không đọc lại bản PDF đã copy vào Bronze. Silver checkpoint kiểm tra `parsed.full_markdown` và bảng parser trả về sau khi Silver đã được persist; nó không đọc lại các hàng Silver để kiểm chứng round-trip. ChunkBuilder thì đọc provisions đã materialize trong Silver.

---

Quan hệ hiện tại:


![](data:image/svg+xml;charset=utf-8,%3Csvg%20font-family%3D%22-apple-system-body%2C%20ui-sans-serif%2C%20-apple-system%2C%20system-ui%2C%20%26quot%3BSegoe%20UI%26quot%3B%2C%20Helvetica%2C%20%26quot%3BApple%20Color%20Emoji%26quot%3B%2C%20Arial%2C%20sans-serif%2C%20%26quot%3BSegoe%20UI%20Emoji%26quot%3B%2C%20%26quot%3BSegoe%20UI%20Symbol%26quot%3B%22%20font-weight%3D%22400%22%20data-d-component%3D%22svg%22%20fill%3D%22currentColor%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%20style%3D%22color%3Argb\(13%2C%2013%2C%2013\)%22%20viewBox%3D%220%200%20340%20220%22%20width%3D%22100%25%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cdefs%3E%3Cmarker%20id%3D%22arr%22%20markerWidth%3D%2210%22%20markerHeight%3D%2210%22%20refX%3D%225%22%20refY%3D%225%22%20orient%3D%22auto%22%3E%3Cpath%20d%3D%22M0%2C0%20L10%2C5%20L0%2C10%20z%22%20fill%3D%22%2364748B%22%2F%3E%3C%2Fmarker%3E%3C%2Fdefs%3E%3Crect%20x%3D%2290%22%20y%3D%2218%22%20width%3D%22160%22%20height%3D%2228%22%20rx%3D%228%22%20fill%3D%22%23DBEAFE%22%20stroke%3D%22%232563EB%22%2F%3E%3Ctext%20x%3D%22170%22%20y%3D%2236%22%20font-size%3D%2210%22%20font-family%3D%22Arial%22%20text-anchor%3D%22middle%22%20fill%3D%22%231D4ED8%22%3Eprepare_gold_dataset%3C%2Ftext%3E%3Crect%20x%3D%2210%22%20y%3D%2278%22%20width%3D%2298%22%20height%3D%2228%22%20rx%3D%228%22%20fill%3D%22%23F3F4F6%22%20stroke%3D%22%239CA3AF%22%2F%3E%3Ctext%20x%3D%2259%22%20y%3D%2296%22%20font-size%3D%229%22%20font-family%3D%22Arial%22%20text-anchor%3D%22middle%22%20fill%3D%22%23111827%22%3EPDFParser%3C%2Ftext%3E%3Crect%20x%3D%22122%22%20y%3D%2278%22%20width%3D%2298%22%20height%3D%2228%22%20rx%3D%228%22%20fill%3D%22%23F3F4F6%22%20stroke%3D%22%239CA3AF%22%2F%3E%3Ctext%20x%3D%22171%22%20y%3D%2296%22%20font-size%3D%229%22%20font-family%3D%22Arial%22%20text-anchor%3D%22middle%22%20fill%3D%22%23111827%22%3EContract%3C%2Ftext%3E%3Crect%20x%3D%22234%22%20y%3D%2278%22%20width%3D%2296%22%20height%3D%2228%22%20rx%3D%228%22%20fill%3D%22%23F3F4F6%22%20stroke%3D%22%239CA3AF%22%2F%3E%3Ctext%20x%3D%22282%22%20y%3D%2296%22%20font-size%3D%229%22%20font-family%3D%22Arial%22%20text-anchor%3D%22middle%22%20fill%3D%22%23111827%22%3ELakehouse%3C%2Ftext%3E%3Crect%20x%3D%22122%22%20y%3D%22152%22%20width%3D%2298%22%20height%3D%2228%22%20rx%3D%228%22%20fill%3D%22%23F3E8FF%22%20stroke%3D%22%237C3AED%22%2F%3E%3Ctext%20x%3D%22171%22%20y%3D%22170%22%20font-size%3D%229%22%20font-family%3D%22Arial%22%20text-anchor%3D%22middle%22%20fill%3D%22%235B21B6%22%3EChunkBuilder%3C%2Ftext%3E%3Cline%20x1%3D%22145%22%20y1%3D%2246%22%20x2%3D%2272%22%20y2%3D%2278%22%20stroke%3D%22%2364748B%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arr\)%22%2F%3E%3Cline%20x1%3D%22170%22%20y1%3D%2246%22%20x2%3D%22171%22%20y2%3D%2278%22%20stroke%3D%22%2364748B%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arr\)%22%2F%3E%3Cline%20x1%3D%22195%22%20y1%3D%2246%22%20x2%3D%22268%22%20y2%3D%2278%22%20stroke%3D%22%2364748B%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arr\)%22%2F%3E%3Cline%20x1%3D%22282%22%20y1%3D%22106%22%20x2%3D%22185%22%20y2%3D%22152%22%20stroke%3D%22%2364748B%22%20stroke-width%3D%221.5%22%20marker-end%3D%22url\(%23arr\)%22%2F%3E%3C%2Fsvg%3E)

---

README nói 4 layer:

| Layer      | Checkpoint                        |
| ---------- | --------------------------------- |
| Schema     | Pre-Bronze + Bronze               |
| Temporal   | Silver checkpoint (parser output) |
| Quality    | Silver checkpoint (parser output) |
| Provenance | Pre-Bronze + Bronze + Gold        |

---

Silver hiện giữ:
- parsed pages trong `silver_pages`
- hierarchical provisions trong `silver_provisions`
- `page_segments_json` trên từng provision để giữ page provenance

Code tạo:

```
ParsedArticle.page_segments → ProvisionParser → Silver page_segments_json
```

mỗi segment có:

```
{
    physical_file_id,
    file_path,
    part_no,
    physical_page,
    global_page,
    text
}
```

Sau đó Silver lưu nguyên JSON. ChunkBuilder đọc đúng JSON này => provenance không bị mất

Article extraction dùng raw_text chứ không dùng markdown. Điều này đúng vì markdown có comment:

```
<!-- PageProvenance -->
```

Nếu dùng markdown thì offset sẽ sai.

---

README ghi:

> Gold chunk được tạo từ Silver provision, page-aware, deterministic.

Code:

```
page_segments
    ↓
_split_text()
    ↓
chunk_id
chunk_hash
```

---

README có nhắc:

> Cross-document temporal không thuộc executable document contract.

Code tách riêng.

Quan hệ:

```
238 amends 168
```

sẽ validate:
- issued_after
- effective_after

Không phụ thuộc Gold => hợp lý

---

1. Recall@K (`retrieval_recall_at_k`)

- **Công thức:**
    $$\text{Recall@K} = \frac{\vert{}\text{gold provisions} \cap \text{retrieved top-K provisions}\vert{}}{\vert{}\text{gold provisions}\vert{}}$$
- **Logic:**
    - Chỉ lấy $K$ chunk đầu tiên trong `retrieved_chunks`.
    - Nếu mẫu có `gold_provisions`, chấm theo định danh provision (`provision_id`, hoặc `logical_doc_id` + `provision_path`).
    - Chỉ lấy các chunk trong top-$K$; một provision được retrieve nếu xuất hiện trong ít nhất một chunk đó.
    - Mẫu benchmark cũ không có `gold_provisions` dùng `gold_chunk_ids` làm dự phòng. Nếu tập gold tương ứng rỗng, trả về `0.0`.

2. Temporal Validity (`temporal_validity_accuracy`)

- Công thức:
$$\text{Temporal Validity} = \frac{\text{số kết quả retrieve đánh giá được và hợp lệ trong top-K}}{\text{số kết quả retrieve đánh giá được trong top-K}}$$

- **Điều kiện hợp lệ:** Một chunk được coi là hợp lệ khi thỏa mãn đồng thời:    $$\text{effective\_from} \leq t_{\text{event}} \quad \text{và} \quad (\text{effective\_to} = \text{None} \;\text{hoặc}\; t_{\text{event}} < \text{effective\_to})$$
- **Logic chi tiết:**
    - Chỉ xét tối đa K kết quả retrieve đầu tiên.
    - Một kết quả đánh giá được khi `effective_from` đã biết. Kết quả chưa biết ngày hiệu lực bị loại khỏi cả tử số và mẫu số; `effective_to = NULL` được hiểu là không có cận trên đã biết.
    - Nếu không có kết quả nào đánh giá được (kể cả không có kết quả retrieve hoặc thiếu `t_event`), metric của mẫu là `N/A` (`null` trong JSON), không phải `0`.
    - Điểm tổng là trung bình các mẫu có giá trị Temporal Validity; mẫu `N/A` bị loại khỏi phép tính. Báo cáo kèm số mẫu đánh giá được.

3. Invalid Citation Rate (`invalid_citation_rate`)

- **Công thức:**    $$\text{Invalid Citation Rate} = \frac{\vert{}\text{predicted\_citations} \setminus \text{gold\_citation\_doc\_ids}\vert{}}{\vert{}\text{predicted\_citations}\vert{}}$$
- **Logic:**
    - Trích xuất các mã văn bản pháp luật từ câu trả lời bằng regex.
    - Chuẩn hóa về chữ hoa + NFC.
    - Citation được tính theo tập mã văn bản duy nhất; nếu model không trích dẫn mã nào $\rightarrow$ trả về `0.0`.
    - Chỉ kiểm tra sự tồn tại trong `gold_citation_doc_ids`, không kiểm tra xem citation đó có thực sự support nội dung câu trả lời hay không.

3. Faithfulness – Lexical Overlap (`answer_faithfulness`)

- **Công thức:**
    $$\text{Faithfulness} = \frac{\vert{}\text{answer\_tokens} \cap \text{evidence\_tokens}\vert{}}{\vert{}\text{answer\_tokens}\vert{}}$$
- **Logic:**
    - `evidence` = nối toàn bộ text của top-$K$ chunk.
    - Token sau chuẩn hóa được biểu diễn bằng tập hợp; từ lặp trong answer hoặc evidence chỉ được tính một lần.
    - Tokenize cả `answer` và `evidence` sau khi:
        - Chuẩn hóa Unicode (NFC).
        - Chuyển về chữ thường.
        - Loại bỏ dấu câu.
        - Giữ lại token có độ dài $> 1$, hoặc là số, hoặc là chữ cái đơn.
    - Nếu `answer` không còn token nào $\rightarrow$ trả về `0.0`.

> **Lưu ý:** Đây là lexical-overlap proxy cho faithfulness ở phiên bản seminar, không phải semantic/NLI-based faithfulness. Thesis có thể mở rộng bằng HART.

---

