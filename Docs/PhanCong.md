
### **Như An**: Tạo file giả lập lỗi thời gian (temporal_metadata_corrupted.pdf), load vào data/incomming/

Nội dung PDF gợi ý:

```
NGHỊ ĐỊNH 999/2026/NĐ-CP
Về việc quy định một số nội dung giả lập

Số: 999/2026/NĐ-CP

Hà Nội, ngày 15 tháng 6 năm 2026

NGHỊ ĐỊNH

Điều 1. Phạm vi điều chỉnh
Nghị định này là tài liệu giả lập được sử dụng để kiểm thử
Temporal Contract của hệ thống VERITAS-RAG.

Điều 2. Thời gian có hiệu lực
Nghị định này có hiệu lực kể từ ngày 01 tháng 6 năm 2026.

Điều 3. Điều khoản thi hành
Tài liệu này chỉ phục vụ mục đích kiểm thử hệ thống.
```

Parser/metadata extraction sẽ tạo đại khái:

```
doc_id          = 999/2026/NĐ-CP
issued_date     = 2026-06-15
effective_from  = 2026-06-01
```

Trong khi Contract quy định:

```
effective_from >= issued_date
```

Nhưng:

```
2026-06-01 < 2026-06-15
```

→ **FAIL Temporal Contract**.
