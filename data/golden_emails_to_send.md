# 📬 Golden Test Dataset: 10 Email Thực Nghiệm Gửi Trực Tiếp & Target Đầu Ra Chuẩn

Tài liệu này chứa **10 mẫu email thực nghiệm chuẩn** (giữ nguyên 100% nội dung email để bạn gửi vào hệ thống) và **bổ sung Target Đầu Ra Kỳ Vọng (Expected Output / Target Action Plan)** mà Cowork Agent cần sinh ra.

> [!NOTE]
> **Quy tắc đánh giá (Evaluation Rule):**
> Kết quả sinh ra từ hệ thống chỉ cần **tương đương về mặt ngữ nghĩa (Semantically Equivalent)**, chứa đủ các ý chính, số liệu, căn cứ và bước hành động cốt lõi, không bắt buộc phải trùng khớp từng câu chữ.

---

## 📋 Bảng Tóm Tắt 10 Test Cases & Target Phân Loại

| STT | Phân loại Intent | Tiêu đề Email | File RAG mục tiêu | Kết quả Target mong đợi |
| :--- | :--- | :--- | :--- | :--- |
| **01** | `RETRIEVE_RAG` | Yêu cầu tư vấn thủ tục cấp Giấy chứng nhận quyền sử dụng đất theo Luật Đất đai 2024 | [`31-2024-qh15-523642.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/31-2024-qh15-523642.md) | Sinh Action Plan tư vấn cấp Sổ đỏ lần đầu theo Luật Đất đai 2024 |
| **02** | `RETRIEVE_RAG` | Hỏi thủ tục đăng ký tạm trú theo Thông tư 53/2025/TT-BCA cho nhân sự mới | [`dang-ky-tam-tru.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/dang-ky-tam-tru.md) | Sinh checklist tạm trú: Mẫu CT01, hạn 03 ngày, lệ phí 7k/15k |
| **03** | `RETRIEVE_RAG` | Hỏi quy trình đăng ký cấp lại CCCD qua VNeID và địa điểm làm thẻ từ 01/07/2026 | [`cap-lai-cccd.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/cap-lai-cccd.md) | Hướng dẫn VNeID DVC dân cư, Điều 27 Luật Căn cước làm thẻ từ 01/07/2026 |
| **04** | `RETRIEVE_RAG` | Hỏi hướng dẫn nộp hồ sơ đại học năm 2026 cho học sinh lớp 12 | [`huong-dan-nop-ho-so-dai-hoc-vinuni.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/huong-dan-nop-ho-so-dai-hoc-vinuni.md) | Liệt kê hồ sơ lớp 12 (02 phiếu ĐK, ảnh 4x6, photo CCCD), nộp trực tuyến |
| **05** | `RETRIEVE_RAG` | Tư vấn quy trình đăng ký kết hôn online trên Cổng Dịch vụ công Quốc gia và lệ phí | [`dang-ky-ket-hon.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/dang-ky-ket-hon.md) | Hướng dẫn nộp DVCQG qua VNeID mức 2, cả hai cùng ký tại UBND xã, miễn phí |
| **06** | `RETRIEVE_RAG` | Kiểm tra thủ tục đăng ký BHXH bắt buộc theo QĐ 863/QĐ-BNV và nộp tờ khai thuế qua cổng DVC TTHC | [`thu-tuc-dang-ky-bhxh-luatvietnam.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/thu-tuc-dang-ky-bhxh-luatvietnam.md)<br>[`thue-dien-tu.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/thue-dien-tu.md) | Mẫu TK1-TS, hạn nộp 30 ngày, xử lý 05 ngày; Cổng DVC TTHC nộp thuế |
| **07** | `RETRIEVE_RAG` | Thủ tục đăng ký xe online lần đầu theo Quyết định 1383/QĐ-BCA và thời hạn giải quyết | [`dang-ky-xe.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/dang-ky-xe.md) | VNeID mức 2, chọn biển số, hạn cấp mới không quá 02 ngày làm việc |
| **08** | `DIRECT_PLAN` | Lịch họp bàn giao kết quả Sprint 5 và chuẩn bị Release | *(Self-contained)* | Trích xuất task trực tiếp từ email (chuẩn bị báo cáo, demo, họp 14h00 25/08) |
| **09** | `NO_ACTION` | [Báo cáo hệ thống] Báo cáo sao lưu dữ liệu tự động tuần 34 hoàn tất | *(Informational)* | Nhận diện thông báo hệ thống tự động, KHÔNG tạo task |
| **10** | `NO_ACTION` | [Ưu đãi 40%] Trọn gói phòng hội nghị và tiệc tri ân khách hàng tháng 9 | *(Marketing/Spam)* | Nhận diện email quảng cáo tiếp thị bên ngoài, KHÔNG tạo task |

---

## ✉️ Chi Tiết 10 Mẫu Email & Target Đầu Ra Hợp Lý

---

### 🟢 EMAIL 01: Tư vấn cấp Sổ đỏ theo Luật Đất đai 2024

#### 📌 Subject (Giữ nguyên để gửi):
```text
Yêu cầu tư vấn thủ tục cấp Giấy chứng nhận quyền sử dụng đất theo Luật Đất đai 2024
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Kính gửi Ban Pháp chế và Bộ phận Hỗ trợ,

Tôi hiện đang có thửa đất nông nghiệp tại địa phương muốn làm thủ tục xin cấp Giấy chứng nhận quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất lần đầu và xin chuyển mục đích sang đất ở theo quy định mới nhất của Luật Đất đai số 31/2024/QH15.

Nhờ anh/chị hỗ trợ tư vấn các nội dung sau:
1. Điều kiện và thành phần hồ sơ bắt buộc cần nộp để cấp Giấy chứng nhận lần đầu theo Luật Đất đai 2024.
2. Cơ quan nào có thẩm quyền tiếp nhận hồ sơ (Chi nhánh Văn phòng đăng ký đất đai hay UBND cấp xã)?
3. Trình tự thực hiện nghĩa vụ tài chính và nhận kết quả.

Xin cảm ơn và mong sớm nhận được phản hồi,
Nguyễn Văn An - SĐT: 0912345678
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `RETRIEVE_RAG` (hoặc `action_required`)
* **Trích dẫn nguồn tài liệu:** [`31-2024-qh15-523642.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/31-2024-qh15-523642.md) (Luật Đất đai số 31/2024/QH15).
* **Tiêu đề Task sinh ra:** Tư vấn hồ sơ và quy trình cấp Giấy chứng nhận quyền sử dụng đất theo Luật Đất đai 2024.
* **Tóm tắt (Summary):** Hướng dẫn Nguyễn Văn An điều kiện, cơ quan thẩm quyền và các bước thực hiện nghĩa vụ tài chính khi cấp Giấy chứng nhận quyền sử dụng đất lần đầu và chuyển mục đích từ đất nông nghiệp sang đất ở.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Tra cứu các điều khoản trong Luật Đất đai số 31/2024/QH15 về điều kiện cấp Giấy chứng nhận quyền sử dụng đất lần đầu và chuyển mục đích sử dụng đất.
  2. Tổng hợp danh mục hồ sơ xin cấp Giấy chứng nhận (đơn đăng ký, giấy tờ chứng minh nguồn gốc đất, trích lục bản đồ địa chính).
  3. Hướng dẫn cơ quan tiếp nhận hồ sơ: Chi nhánh Văn phòng Đăng ký đất đai hoặc Bộ phận một cửa / UBND cấp xã nơi có đất.
  4. Hướng dẫn các bước nộp tiền sử dụng đất, lệ phí trước bạ và phí thẩm định theo thông báo thuế.
  5. Soạn thảo email tư vấn phản hồi gửi cho Nguyễn Văn An (SĐT: 0912345678).
* **Tiêu chí đạt (Acceptance Criteria):** Nhận diện đúng Luật Đất đai 2024 (31/2024/QH15), nêu được cơ quan tiếp nhận và có checklist hồ sơ + nghĩa vụ tài chính.

---

### 🟢 EMAIL 02: Hướng dẫn đăng ký tạm trú theo Thông tư 53/2025/TT-BCA

#### 📌 Subject (Giữ nguyên để gửi):
```text
Hỏi thủ tục đăng ký tạm trú theo Thông tư 53/2025/TT-BCA cho nhân sự mới
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Chào bộ phận Hành chính Nhân sự,

Công ty mình vừa tiếp nhận 2 nhân sự mới chuyển công tác ra Hà Nội và đang thuê nhà tại quận Cầu Giấy. 

Nhờ HR hướng dẫn:
1. Biểu mẫu tờ khai thay đổi thông tin cư trú hiện nay áp dụng mẫu nào (có phải Mẫu CT01 theo Thông tư 53/2025/TT-BCA không)?
2. Nộp hồ sơ trực tuyến qua Cổng DVC / VNeID hay nộp trực tiếp tại Công an cấp xã?
3. Mức lệ phí nộp trực tiếp và trực tuyến là bao nhiêu (15.000đ hay 7.000đ)?
4. Thời hạn giải quyết thủ tục tạm trú là bao nhiêu ngày làm việc kể từ khi nhận đủ hồ sơ?

Nhờ mọi người lên checklist hướng dẫn các bạn nhé.

Trân trọng,
Trang Lê - HR Department
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `RETRIEVE_RAG`
* **Trích dẫn nguồn tài liệu:** [`dang-ky-tam-tru.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/dang-ky-tam-tru.md)
* **Tiêu đề Task sinh ra:** Hướng dẫn thủ tục đăng ký tạm trú cho 2 nhân sự mới.
* **Tóm tắt (Summary):** Cung cấp biểu mẫu Mẫu CT01 (Thông tư 53/2025/TT-BCA), hình thức nộp online/trực tiếp, lệ phí 7.000đ/15.000đ và thời hạn 03 ngày làm việc để đăng ký tạm trú tại Cầu Giấy.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Hướng dẫn 2 nhân sự điền Tờ khai thay đổi thông tin cư trú (Mẫu CT01 theo Thông tư 53/2025/TT-BCA) và chuẩn bị bản sao CCCD, Hợp đồng thuê nhà.
  2. Hướng dẫn nộp hồ sơ qua Cổng DVC Quản lý cư trú / ứng dụng VNeID (lệ phí 7.000đ/lần) hoặc nộp trực tiếp tại Công an phường/xã sở tại (lệ phí 15.000đ/lần).
  3. Thông báo thời hạn giải quyết: 03 ngày làm việc kể từ khi nhận đủ hồ sơ hợp lệ.
  4. Theo dõi tiếp nhận Phiếu hẹn (Mẫu CT04) hoặc Thông báo kết quả đăng ký cư trú (Mẫu CT08) và phản hồi cho Trang Lê.
* **Tiêu chí đạt (Acceptance Criteria):** Nhắc đúng biểu mẫu Mẫu CT01, thời hạn giải quyết 03 ngày làm việc và mức lệ phí (7.000đ online / 15.000đ trực tiếp).

---

### 🟢 EMAIL 03: Cấp lại thẻ CCCD qua VNeID và địa điểm làm thẻ từ 01/07/2026

#### 📌 Subject (Giữ nguyên để gửi):
```text
Hỏi quy trình đăng ký cấp lại CCCD qua VNeID và địa điểm làm thẻ từ 01/07/2026
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Chào anh/chị,

Tôi vừa bị mất thẻ Căn cước công dân gắn chip. Sang tuần cần thẻ để đi công chứng và giao dịch.

Anh/chị cho tôi hỏi:
1. Các bước đăng ký cấp lại CCCD online qua Cổng dịch vụ công quản lý cư trú (dichvucong.dancuquocgia.gov.vn) bằng tài khoản VNeID như thế nào?
2. Sau khi kê khai và thanh toán trực tuyến, đến cơ quan Công an làm những bước gì (chụp ảnh, thu nhận vân tay, sinh trắc học)?
3. Theo Luật Căn cước 2023 (sửa đổi 2025), từ ngày 01/07/2026 người dân có thể làm thủ tục cấp thẻ Căn cước ở đâu?

Cảm ơn anh/chị rất nhiều,
Phạm Văn Minh
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `RETRIEVE_RAG`
* **Trích dẫn nguồn tài liệu:** [`cap-lai-cccd.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/cap-lai-cccd.md)
* **Tiêu đề Task sinh ra:** Hướng dẫn thủ tục cấp lại thẻ Căn cước công dân bị mất qua VNeID.
* **Tóm tắt (Summary):** Hướng dẫn các bước đăng ký cấp lại CCCD trên cổng DVC dân cư bằng tài khoản VNeID, các thủ tục thu nhận sinh trắc học tại Công an và địa điểm làm thẻ theo Điều 27 Luật Căn cước.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Hướng dẫn các bước online: Đăng nhập `dichvucong.dancuquocgia.gov.vn` bằng tài khoản VNeID, chọn Cấp lại thẻ Căn cước, hoàn thiện tờ khai điện tử, chọn hình thức nhận thẻ (bưu chính/Công an) và thanh toán lệ phí.
  2. Hướng dẫn thủ tục trực tiếp: Đến cơ quan Công an theo lịch hẹn để chụp ảnh khuôn mặt, thu nhận vân tay và sinh trắc học.
  3. Trích dẫn địa điểm làm thẻ từ 01/07/2026 theo Điều 27 Luật Căn cước: Cơ quan quản lý căn cước Công an cấp tỉnh, thành phố, huyện, xã, phường, đặc khu hoặc Bộ Công an.
  4. Gửi email phản hồi hướng dẫn chi tiết cho anh Phạm Văn Minh.
* **Tiêu chí đạt (Acceptance Criteria):** Hướng dẫn đúng quy trình VNeID trên DVC dân cư, bước chụp ảnh/thu nhận vân tay tại Công an, và trích dẫn Điều 27 Luật Căn cước về địa điểm làm thẻ từ 01/07/2026.

---

### 🟢 EMAIL 04: Hướng dẫn nộp hồ sơ đại học năm 2026 cho học sinh lớp 12

#### 📌 Subject (Giữ nguyên để gửi):
```text
Hỏi hướng dẫn nộp hồ sơ đại học năm 2026 cho học sinh lớp 12
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Kính gửi Ban Tư vấn Tuyển sinh,

Em là học sinh lớp 12 đang chuẩn bị hồ sơ xét tuyển đại học năm 2026 theo hướng dẫn tuyển sinh từ nguồn Đại học VinUni.

Nhờ Thầy/Cô hướng dẫn giúp em:
1. Học sinh lớp 12 cần chuẩn bị các giấy tờ gì trong bộ hồ sơ (số lượng phiếu đăng ký dự thi, bản photo CCCD, ảnh 4x6 cm, phong bì dán tem)?
2. Quy trình nộp hồ sơ trực tuyến trên hệ thống thisinh.thitotnghiepthpt.edu.vn của Bộ GD&ĐT gồm các bước nào?
3. Khi đăng ký xét tuyển riêng tại cổng thông tin của trường đại học thì cần thực hiện những bước gì?

Em cảm ơn Thầy/Cô rất nhiều ạ!
Hoàng Thị Mai
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `RETRIEVE_RAG`
* **Trích dẫn nguồn tài liệu:** [`huong-dan-nop-ho-so-dai-hoc-vinuni.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/huong-dan-nop-ho-so-dai-hoc-vinuni.md)
* **Tiêu đề Task sinh ra:** Tư vấn hồ sơ và quy trình nộp xét tuyển đại học năm 2026.
* **Tóm tắt (Summary):** Hướng dẫn chi tiết danh mục hồ sơ cho học sinh lớp 12, quy trình nộp trực tuyến trên hệ thống của Bộ GD&ĐT và đăng ký xét tuyển riêng tại trường đại học.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Liệt kê danh mục hồ sơ cho học sinh lớp 12: 02 phiếu đăng ký dự thi (phiếu số 1 và số 2), bản photocopy CMND/CCCD 2 mặt trên cùng 1 mặt A4, 02 ảnh 4x6 cm (ghi rõ thông tin phía sau) + 01 ảnh dán vào phiếu, 02 phong bì dán sẵn tem, giấy tờ ưu tiên (nếu có).
  2. Hướng dẫn quy trình 6 bước nộp hồ sơ trên cổng `thisinh.thitotnghiepthpt.edu.vn` (đăng nhập bằng CCCD/VNeID, đổi mật khẩu, nhập thông tin, lưu và in phiếu).
  3. Hướng dẫn quy trình nộp hồ sơ xét tuyển riêng tại cổng thông tin của trường (đăng ký ngành, tải bản scan minh chứng, nộp lệ phí và lưu mã hồ sơ).
  4. Gửi email phản hồi tư vấn cho thí sinh Hoàng Thị Mai.
* **Tiêu chí đạt (Acceptance Criteria):** Nêu đúng danh mục giấy tờ lớp 12 (02 phiếu, ảnh 4x6, photo CCCD, phong bì tem) và cổng nộp `thisinh.thitotnghiepthpt.edu.vn` / xét tuyển riêng.

---

### 🟢 EMAIL 05: Thủ tục đăng ký kết hôn online và miễn lệ phí

#### 📌 Subject (Giữ nguyên để gửi):
```text
Tư vấn quy trình đăng ký kết hôn online trên Cổng Dịch vụ công Quốc gia và lệ phí
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Chào bộ phận Pháp lý,

Tôi và bạn gái chuẩn bị đăng ký kết hôn trong nước tại UBND cấp xã.

Nhờ anh/chị giải đáp giúp:
1. Quy trình đăng ký kết hôn online trên Cổng dịch vụ công quốc gia (dichvucong.gov.vn) bằng tài khoản VNeID mức độ 2 gồm những bước nào?
2. Khi đến UBND cấp xã nhận kết quả thì cần mang giấy tờ gì và có phải cả hai người cùng ký Giấy chứng nhận kết hôn và Sổ hộ tịch không?
3. Đăng ký kết hôn giữa hai công dân Việt Nam trong nước có phải nộp lệ phí không?

Cảm ơn anh/chị,
Đoàn Quốc Bảo - 0988776655
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `RETRIEVE_RAG`
* **Trích dẫn nguồn tài liệu:** [`dang-ky-ket-hon.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/dang-ky-ket-hon.md)
* **Tiêu đề Task sinh ra:** Hướng dẫn thủ tục đăng ký kết hôn online trên Cổng Dịch vụ công Quốc gia.
* **Tóm tắt (Summary):** Tư vấn quy trình nộp hồ sơ kết hôn trực tuyến qua Cổng DVCQG bằng VNeID mức độ 2, thủ tục đối chiếu giấy tờ gốc tại UBND cấp xã và xác nhận miễn lệ phí kết hôn trong nước.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Hướng dẫn các bước nộp trực tuyến: Đăng nhập `dichvucong.gov.vn` bằng VNeID mức độ 2, chọn thủ tục Đăng ký kết hôn tại UBND cấp xã, khai thông tin và đính kèm hồ sơ điện tử/mẫu hộ tịch tương tác.
  2. Hướng dẫn khi nhận kết quả: Mang theo bản chính/chứng thực giấy tờ tùy thân và Giấy xác nhận tình trạng hôn nhân để đối chiếu, cả hai bên nam nữ bắt buộc phải có mặt cùng ký Giấy chứng nhận kết hôn và Sổ hộ tịch tại UBND cấp xã.
  3. Xác nhận chính sách: Miễn lệ phí đăng ký kết hôn đối với công dân Việt Nam kết hôn với nhau trong nước.
  4. Gửi email phản hồi tư vấn cho anh Đoàn Quốc Bảo.
* **Tiêu chí đạt (Acceptance Criteria):** Nêu đúng cổng `dichvucong.gov.vn`, tài khoản VNeID mức 2, quy định cả hai cùng ký tại UBND xã và xác nhận miễn lệ phí trong nước.

---

### 🟢 EMAIL 06: Đăng ký BHXH (QĐ 863/QĐ-BNV) & Nộp thuế trên Cổng DVC TTHC

#### 📌 Subject (Giữ nguyên để gửi):
```text
Kiểm tra thủ tục đăng ký BHXH bắt buộc theo QĐ 863/QĐ-BNV và nộp tờ khai thuế qua cổng DVC TTHC
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Kính gửi Ban Giám đốc và Anh Dũng,

Phòng Kế toán cần rà soát 2 nội dung nghiệp vụ:
1. Theo Quyết định 863/QĐ-BNV năm 2025, doanh nghiệp cần chuẩn bị tờ khai gì cho 5 nhân sự mới tham gia BHXH bắt buộc (có cần Mẫu TK1-TS của người lao động không), thời hạn nộp hồ sơ (30 ngày) và thời hạn cơ quan BHXH giải quyết cấp sổ là bao nhiêu ngày làm việc (05 ngày)?
2. Cục Thuế triển khai Cổng Dịch vụ công TTHC tại dichvucong.gdt.gov.vn thay thế Thuế điện tử. Nhờ anh hướng dẫn các bước nộp tờ khai online (vào mục Dịch vụ khác -> Hồ sơ khai thuế khác -> Trình ký -> Ký và nộp tờ khai).

Nhờ anh lập checklist kế hoạch để team thực hiện đúng quy chuẩn.

Trần Thu Hà - Kế toán trưởng
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `RETRIEVE_RAG`
* **Trích dẫn nguồn tài liệu:** [`thu-tuc-dang-ky-bhxh-luatvietnam.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/thu-tuc-dang-ky-bhxh-luatvietnam.md) & [`thue-dien-tu.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/thue-dien-tu.md)
* **Tiêu đề Task sinh ra:** Lập kế hoạch đăng ký BHXH bắt buộc và nộp tờ khai thuế trực tuyến quý 3.
* **Tóm tắt (Summary):** Lập checklist đăng ký tham gia BHXH cho 5 nhân sự mới theo QĐ 863/QĐ-BNV và hướng dẫn các bước nộp tờ khai trên Cổng DVC TTHC `dichvucong.gdt.gov.vn`.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Hoàn thiện hồ sơ BHXH: Thu thập Tờ khai Mẫu TK1-TS của 5 nhân sự mới và Tờ khai kèm danh sách lao động của người sử dụng lao động.
  2. Nộp hồ sơ BHXH trong thời hạn 30 ngày kể từ ngày thuộc đối tượng; theo dõi tiến độ cấp Sổ BHXH/Thẻ BHYT trong 05 ngày làm việc.
  3. Hướng dẫn nộp tờ khai thuế trên Cổng DVC TTHC (`dichvucong.gdt.gov.vn`): Đăng nhập tài khoản định danh doanh nghiệp -> Dịch vụ khác -> Hồ sơ khai thuế khác -> Trình ký -> Ký số và nộp tờ khai.
  4. Đôn đốc kế toán hoàn tất trước ngày 20/09 và phản hồi email cho Kế toán trưởng Trần Thu Hà.
* **Tiêu chí đạt (Acceptance Criteria):** Nhắc đúng QĐ 863/QĐ-BNV (Mẫu TK1-TS, hạn nộp 30 ngày, xử lý 05 ngày làm việc) và quy trình nộp tờ khai trên Cổng DVC TTHC `dichvucong.gdt.gov.vn`.

---

### 🟢 EMAIL 07: Đăng ký xe online lần đầu theo Quyết định 1383/QĐ-BCA

#### 📌 Subject (Giữ nguyên để gửi):
```text
Thủ tục đăng ký xe online lần đầu theo Quyết định 1383/QĐ-BCA và thời hạn giải quyết
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Anh Dũng xem giúp em thủ tục đăng ký xe theo quy định mới nhé,

Công ty mình chuẩn bị làm thủ tục đăng ký xe trực tuyến lần đầu cho phương tiện mới mua theo Quyết định 1383/QĐ-BCA.

Anh cho em hỏi:
1. Điều kiện thực hiện trên Cổng DVC / ứng dụng VNeID (yêu cầu tài khoản định danh điện tử mức độ mấy)?
2. Các bước lựa chọn biển số định danh, biển trúng đấu giá hoặc bấm biển số mới và nộp lệ phí trực tuyến ra sao?
3. Thời hạn giải quyết cấp mới Chứng nhận đăng ký xe và Biển số xe theo Quyết định 1383/QĐ-BCA là bao nhiêu ngày làm việc (có phải không quá 02 ngày làm việc)?

Nhờ anh lập kế hoạch để triển khai hoàn thành sớm nhé.
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `RETRIEVE_RAG`
* **Trích dẫn nguồn tài liệu:** [`dang-ky-xe.md`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/extracted/dang-ky-xe.md)
* **Tiêu đề Task sinh ra:** Hướng dẫn quy trình đăng ký xe trực tuyến lần đầu theo Quyết định 1383/QĐ-BCA.
* **Tóm tắt (Summary):** Hướng dẫn đăng ký xe online qua VNeID mức độ 2, lựa chọn biển số định danh/đấu giá/bấm mới, nộp lệ phí trực tuyến và nhận kết quả trong không quá 02 ngày làm việc.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Xác nhận điều kiện: Chủ xe đăng nhập Cổng DVC / ứng dụng VNeID bằng tài khoản định danh điện tử mức độ 2.
  2. Kê khai giấy khai đăng ký xe điện tử, chọn loại biển số (biển định danh, biển đấu giá hoặc bấm biển mới) và nộp lệ phí trực tuyến.
  3. Xác nhận thời hạn giải quyết: Cấp mới Chứng nhận đăng ký xe và Biển số không quá 02 ngày làm việc (biển số cấp ngay).
  4. Bố trí người nhận Chứng nhận đăng ký và Biển số xe (trực tiếp hoặc bưu chính) và gửi email phản hồi hướng dẫn cho Trọng Vũ.
* **Tiêu chí đạt (Acceptance Criteria):** Nhắc đúng Quyết định 1383/QĐ-BCA, yêu cầu VNeID mức độ 2, các bước chọn biển số và thời hạn cấp mới không quá 02 ngày làm việc.

---

### 🔵 EMAIL 08: Lịch họp nghiệm thu Sprint 5 (Direct Plan - Không gọi RAG)

#### 📌 Subject (Giữ nguyên để gửi):
```text
Lịch họp bàn giao kết quả Sprint 5 và chuẩn bị Release
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Chào toàn bộ Team,

Chúng ta sẽ tổ chức buổi họp nghiệm thu Sprint 5 và chốt kế hoạch Release v1.2 vào lúc 14:00 chiều mai (25/08) tại phòng họp Lotus (tầng 3) và online qua Google Meet link: meet.google.com/abc-xyz-123.

Yêu cầu công việc cần chuẩn bị:
1. Anh Dũng chuẩn bị báo cáo tiến độ RAG Pipeline và kiểm thử latency.
2. Frontend team demo giao diện danh sách Tasks và Chat SSE streaming.
3. QA team gửi bảng tổng hợp test report trước 11:30 sáng mai.

Mọi người xác nhận tham dự qua lịch calendar nhé.

Thân mến,
PM Team
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `DIRECT_PLAN` (hoặc `action_required` với `retrieval_expected: false` / reason `email_self_contained`)
* **Trích dẫn nguồn tài liệu:** Không cần gọi RAG (None).
* **Tiêu đề Task sinh ra:** Chuẩn bị nội dung và tham gia họp nghiệm thu Sprint 5.
* **Tóm tắt (Summary):** Chuẩn bị báo cáo RAG latency, demo frontend SSE, nhận test report QA trước 11:30 và tham gia họp nghiệm thu Sprint 5 lúc 14:00 ngày 25/08 tại phòng Lotus / Google Meet.
* **Kế hoạch hành động chi tiết (Action Plan Steps):**
  1. Backend (Anh Dũng): Chuẩn bị tài liệu báo cáo tiến độ Email RAG Pipeline và kết quả đo độ trễ (latency).
  2. Frontend: Chuẩn bị kịch bản demo giao diện Tasks và kết nối SSE Chat streaming.
  3. QA Team: Thu thập và kiểm tra bảng tổng hợp test report trước 11:30 sáng ngày 25/08.
  4. Xác nhận lịch Calendar và tham gia họp lúc 14:00 ngày 25/08 tại phòng Lotus (tầng 3) hoặc qua link `meet.google.com/abc-xyz-123`.
* **Tiêu chí đạt (Acceptance Criteria):** Trích xuất đúng mốc 14:00 ngày 25/08, địa điểm phòng Lotus / Google Meet, đủ 3 đầu việc của Backend, Frontend, QA và KHÔNG kích hoạt truy vấn RAG.

---

### ⚪ EMAIL 09: Báo cáo sao lưu tự động định kỳ (Thông báo - Không sinh Task)

#### 📌 Subject (Giữ nguyên để gửi):
```text
[Báo cáo hệ thống] Báo cáo sao lưu dữ liệu tự động tuần 34 hoàn tất
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Thông báo tự động từ Hệ thống Backup & Storage:

Tiến trình sao lưu tự động cơ sở dữ liệu và vector index tuần 34 đã hoàn tất thành công vào lúc 03:00:00 UTC ngày 24/08/2026.
- Tổng dung lượng file backup: 2.4 GB
- Trạng thái kiểm tra tính toàn vẹn (Checksum SHA-256): HỢP LỆ (Passed)
- Vị trí lưu trữ an toàn: S3 Storage (Bucket: backup-cowork-weekly)

Email này được gửi tự động nhằm mục đích lưu trữ thông tin, người nhận không cần thực hiện bất kỳ hành động nào.
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `NO_ACTION` (hoặc `informational`)
* **Tóm tắt (Summary):** Email thông báo hệ thống định kỳ về việc sao lưu dữ liệu tuần 34 thành công (2.4 GB, checksum hợp lệ).
* **Kế hoạch hành động chi tiết:** Không sinh bất kỳ Task nào (No Action Items created).
* **Tiêu chí đạt (Acceptance Criteria):** Phân loại chính xác là `NO_ACTION` / `INFORMATIONAL`, không tạo task rác trong hệ thống.

---

### 🔴 EMAIL 10: Email quảng cáo/khuyến mãi (Marketing/Spam - Không liên quan)

#### 📌 Subject (Giữ nguyên để gửi):
```text
[Ưu đãi 40%] Trọn gói phòng hội nghị và tiệc tri ân khách hàng tháng 9
```

#### 📄 Nội dung Email (Giữ nguyên để gửi):
```text
Kính gửi Quý Khách hàng,

Trung tâm Hội nghị & Khách sạn Grand Palace trân trọng gửi tới Quý Doanh nghiệp chương trình ưu đãi đặc quyền "Mùa Vàng Hội Nghị - Tri Ân Đối Tác":
- Giảm ngay 40% chi phí thuê sảnh tiệc và phòng hội thảo tiêu chuẩn 5 sao.
- Miễn phí toàn bộ hệ thống âm thanh, ánh sáng và màn hình LED P2.5 siêu nét.
- Tặng kèm tiệc teabreak cao cấp cho đoàn từ 50 khách trở lên.

Chương trình áp dụng cho các hợp đồng ký kết trước ngày 15/09/2026. Để nhận báo giá chi tiết, vui lòng truy cập website grandpalace-resort.vn hoặc gọi hotline 1900 8888.

Nếu Quý khách không muốn nhận các email ưu đãi tiếp theo, vui lòng bấm Unsubscribe tại đây.
```

#### 🎯 TARGET ĐẦU RA HỢP LÝ CỦA HỆ THỐNG:
* **Route phân loại:** `NO_ACTION` (hoặc `irrelevant`)
* **Tóm tắt (Summary):** Email tiếp thị quảng cáo ưu đãi 40% thuê phòng hội thảo và dịch vụ tiệc của khách sạn Grand Palace.
* **Kế hoạch hành động chi tiết:** Không sinh bất kỳ Task nào (No Action Items created).
* **Tiêu chí đạt (Acceptance Criteria):** Phân loại chính xác là `NO_ACTION` / `IRRELEVANT` / `MARKETING`, tự động lọc bỏ không đưa vào danh sách công việc.
