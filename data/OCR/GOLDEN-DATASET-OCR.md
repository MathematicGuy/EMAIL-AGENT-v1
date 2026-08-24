# 🏆 GOLDEN DATASET OCR & MULTIMODAL RAG BENCHMARK (V2)
> **Tài liệu nguồn:** `design machine learning systems.pdf`
> **Thư mục ảnh đối chứng:** `data/OCR/`
> **Tập tin JSON:** `data/OCR/GOLDEN-DATASET-OCR.json`
> **Ngày cập nhật:** 24/08/2026

---

## 📌 Bảng Tổng Hợp 7 Test Case OCR

| ID | Figure / Định danh | Tên File Ảnh Thật | Nội Dung / Trọng Tâm | Từ Khóa |
| :---: | :--- | :--- | :--- | :--- |
| **OCR-01** | **Figure 1-1** | `figure_1_1_components_of_an_ml_system.png` | Different components of an ML system | `Figure 1-1, Chapter 11, Chapter 5, Chapter 6...` |
| **OCR-02** | **Figure 1-2** | `figure_1_2_traditional_software_vs_machine_learning.png` | Traditional software vs Machine learning | `Figure 1-2, Traditional software, Machine learning, Inputs...` |
| **OCR-03** | **Figure 1-3** | `figure_1_3_state_of_enterprise_ml_algorithmia.png` | 2020 state of enterprise machine learning (Algorithmia) | `38%, 37%, 34%, Reducing costs...` |
| **OCR-04** | **Figure 1-4** | `figure_1_4_latency_vs_throughput_batching.png` | Latency vs Throughput in single query vs batched queries | `100 queries/s, 50 queries/s, 1,000 queries/s, 2,500 queries/s...` |
| **OCR-05** | **Figure 1-5** | `figure_1_5_data_in_research_vs_production_karpathy.png` | Data in research versus data in production (Andrej Karpathy) | `Andrej Karpathy, PhD, Tesla, Datasets...` |
| **OCR-06** | **Chapter 1 Summary** | `chapter_1_summary_research_vs_production.png` | Summary of ML in enterprise, research vs production differences, and system approach | `stakeholder involvement, computational priority, properties of data, fairness issues...` |
| **OCR-07** | **Chapter 2 Summary** | `chapter_2_summary_requirements_and_data.png` | Four general requirements of ML systems and the role of data | `Reliability, Scalability, Maintainability, Adaptability...` |

---

## 🎯 Chi Tiết 7 Test Case (Prompt Chuẩn Trong Tài Liệu & Target Ground Truth)

### 📸 Test Case 1: Figure 1-1 — Different components of an ML system
* **File đối chứng:** [`data/OCR/figure_1_1_components_of_an_ml_system.png`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/OCR/figure_1_1_components_of_an_ml_system.png)
* **Câu hỏi kiểm thử tiếng Việt (Prompt):**
  > *"Trong tài liệu, sơ đồ Figure 1-1 mô tả các thành phần nào cấu thành một hệ thống ML (ML system) và từng thành phần được trình bày ở những chương (Chapter) nào trong sách?"*
* **Câu hỏi tiếng Anh (Prompt EN):**
  > *"In the document, what components make up an ML system according to Figure 1-1, and in which chapters are they discussed throughout the book?"*
* **Ground Truth Target Answer:**
  * **Tóm tắt:** Sơ đồ Figure 1-1 chỉ ra rằng thuật toán ML chỉ là một phần nhỏ của toàn bộ hệ thống ML, bao gồm các thành phần: ML system users (Chapter 11), Business requirements (Chapters 1 & 2), ML system developers (toàn bộ sách), Deployment, monitoring, updating of logics (Chapters 7, 8 & 9), Feature engineering (Chapter 5), ML algorithms & Evaluation (Chapter 6), Data (Chapters 3 & 4), và Infrastructure (Chapter 10).
  * **Các sự kiện cốt lõi (Key Facts):**
    * ML system users: Chapter 11
    * Business requirements: Chapters 1 & 2
    * Deployment, monitoring, updating of logics: Chapters 7, 8 & 9
    * Feature engineering: Chapter 5
    * ML algorithms & Evaluation: Chapter 6
    * Data: Chapters 3 & 4
    * Infrastructure: Chapter 10
    * ML system developers: Entire book
* **Từ khóa đánh giá (Keywords):** `Figure 1-1, Chapter 11, Chapter 5, Chapter 6, Chapter 10, Feature engineering, Infrastructure, Deployment`

---

### 📸 Test Case 2: Figure 1-2 — Traditional software vs Machine learning
* **File đối chứng:** [`data/OCR/figure_1_2_traditional_software_vs_machine_learning.png`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/OCR/figure_1_2_traditional_software_vs_machine_learning.png)
* **Câu hỏi kiểm thử tiếng Việt (Prompt):**
  > *"Theo sơ đồ Figure 1-2 trong tài liệu, điểm khác biệt cốt lõi về đầu vào (Inputs) và đầu ra (Outputs) giữa phần mềm truyền thống (Traditional software) và Học máy (Machine learning) là gì?"*
* **Câu hỏi tiếng Anh (Prompt EN):**
  > *"According to Figure 1-2 in the document, what is the core difference regarding inputs and outputs between traditional software and machine learning?"*
* **Ground Truth Target Answer:**
  * **Tóm tắt:** Trong phần mềm truyền thống (Traditional software), đầu vào là Inputs và Patterns (quy tắc/mẫu viết tay) để tạo ra Outputs. Ngược lại, trong Machine Learning, đầu vào là Inputs và Outputs để mô hình tự học và sinh ra Patterns.
  * **Các sự kiện cốt lõi (Key Facts):**
    * Traditional software: Inputs + Patterns -> Outputs
    * Machine learning: Inputs + Outputs -> Patterns
    * ML solutions learn patterns from inputs and outputs instead of requiring hand-specified patterns
* **Từ khóa đánh giá (Keywords):** `Figure 1-2, Traditional software, Machine learning, Inputs, Outputs, Patterns, learn patterns`

---

### 📸 Test Case 3: Figure 1-3 — 2020 state of enterprise machine learning (Algorithmia)
* **File đối chứng:** [`data/OCR/figure_1_3_state_of_enterprise_ml_algorithmia.png`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/OCR/figure_1_3_state_of_enterprise_ml_algorithmia.png)
* **Câu hỏi kiểm thử tiếng Việt (Prompt):**
  > *"Dựa vào biểu đồ Figure 1-3 (khảo sát của Algorithmia 2020) trong tài liệu, 3 mục tiêu ứng dụng Machine Learning hàng đầu trong doanh nghiệp chiếm tỷ lệ phần trăm cao nhất là những mục tiêu nào?"*
* **Câu hỏi tiếng Anh (Prompt EN):**
  > *"Based on Figure 1-3 (Algorithmia 2020 survey) in the document, what are the top 3 enterprise machine learning use cases with the highest percentages?"*
* **Ground Truth Target Answer:**
  * **Tóm tắt:** Top 3 mục tiêu ứng dụng ML hàng đầu trong doanh nghiệp năm 2020 theo Algorithmia là: 1. Reducing costs (Giảm chi phí) chiếm 38%; 2. Generating customer insights/intelligence (Tạo thông tin chi tiết/thấu hiểu khách hàng) chiếm 37%; 3. Improving customer experience (Cải thiện trải nghiệm khách hàng) chiếm 34%.
  * **Các sự kiện cốt lõi (Key Facts):**
    * Top 1: Reducing costs - 38%
    * Top 2: Generating customer insights/intelligence - 37%
    * Top 3: Improving customer experience - 34%
    * Source: Adapted from an image by Algorithmia
* **Từ khóa đánh giá (Keywords):** `38%, 37%, 34%, Reducing costs, Generating customer insights, Improving customer experience, Algorithmia`

---

### 📸 Test Case 4: Figure 1-4 — Latency vs Throughput in single query vs batched queries
* **File đối chứng:** [`data/OCR/figure_1_4_latency_vs_throughput_batching.png`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/OCR/figure_1_4_latency_vs_throughput_batching.png)
* **Câu hỏi kiểm thử tiếng Việt (Prompt):**
  > *"Theo Figure 1-4 trong tài liệu, khi xử lý truy vấn đơn lẻ so với xử lý theo lô (batching), mối quan hệ giữa độ trễ (latency) và thông lượng (throughput) thay đổi như thế nào? Nêu ví dụ số liệu minh chứng."*
* **Câu hỏi tiếng Anh (Prompt EN):**
  > *"According to Figure 1-4 in the document, how does the relationship between latency and throughput change when processing queries one at a time versus in batches? Provide numerical examples."*
* **Ground Truth Target Answer:**
  * **Tóm tắt:** Khi xử lý từng truy vấn một (One query at a time), độ trễ cao hơn làm giảm thông lượng (10ms -> 100 queries/s; 20ms -> 50 queries/s). Tuy nhiên, khi xử lý theo lô (Batched queries), độ trễ cao hơn vẫn có thể mang lại thông lượng cao hơn rất nhiều (10ms với 10 queries -> 1,000 queries/s; 20ms với 50 queries -> 2,500 queries/s).
  * **Các sự kiện cốt lõi (Key Facts):**
    * One query at a time: 10ms = 100 queries/s, 20ms = 50 queries/s
    * Batched queries: 10ms (10 queries) = 1,000 queries/s, 20ms (50 queries) = 2,500 queries/s
    * With batching, higher latency can yield significantly higher throughput
* **Từ khóa đánh giá (Keywords):** `100 queries/s, 50 queries/s, 1,000 queries/s, 2,500 queries/s, Batched queries, One query at a time, Latency, Throughput`

---

### 📸 Test Case 5: Figure 1-5 — Data in research versus data in production (Andrej Karpathy)
* **File đối chứng:** [`data/OCR/figure_1_5_data_in_research_vs_production_karpathy.png`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/OCR/figure_1_5_data_in_research_vs_production_karpathy.png)
* **Câu hỏi kiểm thử tiếng Việt (Prompt):**
  > *"Dựa vào biểu đồ Figure 1-5 trích dẫn từ Andrej Karpathy trong tài liệu, sự khác biệt về mối bận tâm/mất ngủ giữa nghiên cứu tiến sĩ (PhD) và môi trường sản xuất tại Tesla đối với Datasets và Models/algorithms là gì?"*
* **Câu hỏi tiếng Anh (Prompt EN):**
  > *"Based on Figure 1-5 adapted from Andrej Karpathy in the document, what is the difference in sleep lost over Datasets versus Models and algorithms between PhD research and production at Tesla?"*
* **Ground Truth Target Answer:**
  * **Tóm tắt:** Biểu đồ "Amount of sleep lost over..." chỉ ra rằng trong nghiên cứu học thuật (PhD), người ta dành phần lớn thời gian (~95%) cho Mô hình và Thuật toán (Models and algorithms) và chỉ một phần rất nhỏ (~5%) cho Dữ liệu (Datasets). Ngược lại, tại Tesla (Production), Dữ liệu (Datasets) chiếm tới khoảng 75% (3/4) mối bận tâm, còn Mô hình và Thuật toán chỉ chiếm 25%.
  * **Các sự kiện cốt lõi (Key Facts):**
    * PhD: Datasets (~5%) vs Models and algorithms (~95%)
    * Tesla: Datasets (~75% / 3 quarters) vs Models and algorithms (~25% / 1 quarter)
    * Source: Adapted from an image by Andrej Karpathy
* **Từ khóa đánh giá (Keywords):** `Andrej Karpathy, PhD, Tesla, Datasets, Models and algorithms, Amount of sleep lost`

---

### 📸 Test Case 6: Chapter 1 Summary — Summary of ML in enterprise, research vs production differences, and system approach
* **File đối chứng:** [`data/OCR/chapter_1_summary_research_vs_production.png`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/OCR/chapter_1_summary_research_vs_production.png)
* **Câu hỏi kiểm thử tiếng Việt (Prompt):**
  > *"Trong phần tóm tắt (Summary) của Chương 1 trong tài liệu, tác giả đã nêu những khía cạnh khác biệt nào giữa ML trong nghiên cứu (research) và ML trong thực tế (production)?"*
* **Câu hỏi tiếng Anh (Prompt EN):**
  > *"In the Chapter 1 Summary in the document, what specific differences between ML in research and ML in production were highlighted by the author?"*
* **Ground Truth Target Answer:**
  * **Tóm tắt:** Phần Summary của Chương 1 chỉ ra các điểm khác biệt giữa ML trong nghiên cứu và ML trong production bao gồm: sự tham gia của các bên liên quan (stakeholder involvement), mức độ ưu tiên tính toán (computational priority), đặc tính của dữ liệu được sử dụng (properties of data used), mức độ nghiêm trọng của vấn đề công bằng (gravity of fairness issues), và các yêu cầu về khả năng diễn giải (requirements for interpretability).
  * **Các sự kiện cốt lõi (Key Facts):**
    * Stakeholder involvement
    * Computational priority
    * Properties of data used
    * Gravity of fairness issues
    * Requirements for interpretability
    * Focusing only on ML algorithms is far from enough; a holistic system approach is required
* **Từ khóa đánh giá (Keywords):** `stakeholder involvement, computational priority, properties of data, fairness issues, interpretability, system approach, holistically`

---

### 📸 Test Case 7: Chapter 2 Summary — Four general requirements of ML systems and the role of data
* **File đối chứng:** [`data/OCR/chapter_2_summary_requirements_and_data.png`](file:///home/dammanhdungvn/Downloads/Workspace/Test/EMAIL-AGENT-v1/data/OCR/chapter_2_summary_requirements_and_data.png)
* **Câu hỏi kiểm thử tiếng Việt (Prompt):**
  > *"Theo phần tóm tắt (Summary) của Chương 2 trong tài liệu, 4 yêu cầu tổng quát (four most general requirements) của một hệ thống ML tốt là gì và những mô hình nào được dẫn chứng để chứng minh tầm quan trọng của dữ liệu?"*
* **Câu hỏi tiếng Anh (Prompt EN):**
  > *"According to the Chapter 2 Summary in the document, what are the four most general requirements of a good ML system, and which models were cited to demonstrate the critical role of data?"*
* **Ground Truth Target Answer:**
  * **Tóm tắt:** Bốn yêu cầu tổng quát của một hệ thống ML tốt là: 1. Reliability (Độ tin cậy), 2. Scalability (Khả năng mở rộng), 3. Maintainability (Khả năng bảo trì), và 4. Adaptability (Khả năng thích ứng). Các hệ thống được dẫn chứng chứng minh tiến bộ của ML phụ thuộc vào lượng dữ liệu khổng lồ gồm: AlexNet, BERT, và GPT.
  * **Các sự kiện cốt lõi (Key Facts):**
    * 4 general requirements: Reliability, Scalability, Maintainability, Adaptability
    * Business objectives must motivate ML objectives (businesses don't care about ML metrics unless they move business metrics)
    * Building an ML system is an iterative process
    * Cited systems for data progress: AlexNet, BERT, and GPT
* **Từ khóa đánh giá (Keywords):** `Reliability, Scalability, Maintainability, Adaptability, AlexNet, BERT, GPT, iterative process, business objectives`

---
