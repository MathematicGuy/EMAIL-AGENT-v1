# Implementation Plan: Option A (Ultra-Clean Connected Timeline) & ChatGPT-Style Live Reasoning

## Goal Description
Implement the approved **Option A ("Ultra-Clean Connected Timeline")** for the **"Chi tiết xử lý" (Execution Trace) Drawer** and the **ChatGPT-style Inline Reasoning Component** across the Cowork Agent chat interface:
1. **Drawer Header Segmented Control**: 2-tab slider switcher between **"Tiến trình xử lý"** (Process Timeline) and **"Bộ nhớ"** (Memory).
2. **Option A Connected 3-Step Timeline**:
   * **Step 1: Hiểu yêu cầu** — Clean routing chip (`RAG · Truy xuất tài liệu` vs `Hội thoại trực tiếp · Direct`).
   * **Step 2: Tìm thông tin liên quan** — Chunks count badge + document file chips (e.g. `📄 cap_lai_cccd.pdf`).
   * **Step 3: Tổng hợp câu trả lời** — Model badge, reasoning time, and formatted Chain of Thought (CoT) block.
3. **Dedicated "Bộ nhớ" Tab**: Clean placeholder card for Episodic Memory, Working Memory, and User Profile sync status.
4. **ChatGPT-Style Inline Reasoning Card**: Live timer during streaming (`🧠 Đang suy luận... 3.8s`) and auto-collapsing pill on completion (`✦ Đã suy luận trong 3,8s · mimo-v2.5-pro [▼]`).

---

## Visual Design Specification (Option A)

```text
┌────────────────────────────────────────────────────────────────────────┐
│ ▤ Chi tiết xử lý                                                    ✕ │
│ mimo · mimo-v2.5-pro · Suy luận                                        │
│                                                                        │
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │  [ ✦ Tiến trình xử lý (3) ]        [ 🧠 Bộ nhớ (Sẵn sàng) ]         │ │  <-- Segmented Slider
│ └────────────────────────────────────────────────────────────────────┘ │
│                                                                        │
│ TIẾN TRÌNH XỬ LÝ THEO BƯỚC                                              │
│                                                                        │
│  ●  1. Hiểu yêu cầu                                        [Hệ thống]  │
│  │  Định tuyến:  [ RAG · Truy xuất tài liệu ]                          │
│  │                                                                     │
│  ●  2. Tìm thông tin liên quan                             [Hệ thống]  │
│  │  Kết quả:     5 đoạn trích liên quan (1 tài liệu)                   │
│  │  Tài liệu:    📄 cap_lai_cccd.pdf                                   │
│  │                                                                     │
│  ●  3. Tổng hợp câu trả lời                           [Lập luận AI]   │
│     Mô hình:     mimo-v2.5-pro · 3.9s                                  │
│     ┌─ Chuỗi suy luận (Chain of Thought) ────────────────────────────┐ │
│     │ Let me analyze this problem step by step...                    │ │
│     │ 1. Train A departs at 8:00 AM @ 60 km/h...                     │ │
│     │ 2. Remaining distance at 9:00 AM is 300 km...                  │ │
│     │ [📋 Sao chép suy luận]                                         │ │
│     └────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Proposed Changes by Component

### 1. [`frontend/src/dashboard/components/ExecutionTraceDrawer.tsx`](file:///C:/WORK/EMAIL-AGENT-v1/frontend/src/dashboard/components/ExecutionTraceDrawer.tsx)
* **Tab State**: Add `activeTab: 'process' | 'memory'` controlled by a sleek segmented slider header.
* **Option A Timeline Structure**:
  * Connected vertical line (`w-px bg-[#413b34]`).
  * Filter/map activities into the 3 standardized steps:
    1. `understanding_request` -> Displays route pill (`RAG` vs `Direct Chat`).
    2. `searching_relevant_information` -> Displays chunk count & document name tags. (If direct chat, shows subtle `Không yêu cầu truy xuất tài liệu`).
    3. `preparing_response` / `preparing_action_plan` -> Displays CoT trace with copy button, or Fast Mode indicator.
* **Memory Tab**:
  * Displays memory source breakdown: Episodic memory turns, working memory buffer status, and user profile state.

### 2. `[NEW]` [`frontend/src/dashboard/components/InlineReasoningCard.tsx`](file:///C:/WORK/EMAIL-AGENT-v1/frontend/src/dashboard/components/InlineReasoningCard.tsx)
* **Live Stopwatch Timer**: Increments live while `isGenerating`.
* **Collapsible Accordion**: Open while generating, auto-collapses to a compact pill on completion.
* **Copy Button & Monospace Font**: Clean formatting with syntax clarity.

### 3. [`frontend/src/dashboard/components/ChatStreamView.tsx`](file:///C:/WORK/EMAIL-AGENT-v1/frontend/src/dashboard/components/ChatStreamView.tsx)
* Embed `InlineReasoningCard` above the assistant markdown response text.

### 4. `[NEW]` [`frontend/src/dashboard/components/InlineReasoningCard.test.tsx`](file:///C:/WORK/EMAIL-AGENT-v1/frontend/src/dashboard/components/InlineReasoningCard.test.tsx) & [`ExecutionTraceDrawer.test.tsx`](file:///C:/WORK/EMAIL-AGENT-v1/frontend/src/dashboard/components/ExecutionTraceDrawer.test.tsx)
* Comprehensive unit tests covering:
  * Tab switching between `process` and `memory`.
  * Connected timeline step rendering with Option A.
  * Inline reasoning card expansion, collapse, and copy actions.

---

## Verification Plan

### Automated Tests
1. **Frontend Unit & Component Tests**:
   ```bash
   cd frontend && pnpm test
   ```
2. **Frontend Typecheck & Linter**:
   ```bash
   cd frontend && pnpm check-types && pnpm lint
   ```
3. **Backend Quality Gates**:
   ```bash
   uv run ruff check . && uv run mypy src
   ```

### Manual Verification
1. Open the Chat Dashboard (`http://localhost:5173/#dashboard`).
2. Ask a reasoning question with **MiMo v2.5 Pro** and **Mistral Medium 3.5**.
3. Verify live stopwatch timer and inline collapsible reasoning card.
4. Click *"Chi tiết xử lý"* to verify the Option A Connected 3-Step Timeline and the Segmented Tab Switcher.
