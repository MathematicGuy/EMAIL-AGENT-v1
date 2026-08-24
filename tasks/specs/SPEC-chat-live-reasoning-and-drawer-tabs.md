# Spec: Execution Trace Drawer Tabs & Reasoning Presentation

## Problem Statement

The "Chi tiết xử lý" (Execution Trace) drawer conflated operational processing steps with memory state in a single vertical list containing verbose boilerplate text, cluttering the technical inspection experience. At the same time, model reasoning leaked into the chat stream itself: an inline "Chuỗi suy luận" pill sat between the progress summary and the answer, competing with the answer for attention on every assistant turn.

## Solution

1. **Reasoning belongs to the drawer, not the stream**: The assistant message bubble shows only the progress summary, the answer, its citations and artifacts. All Chain of Thought (CoT) presentation lives inside the "Chi tiết xử lý" drawer, under step **3. Tổng hợp câu trả lời**.
2. **Drawer Segmented Control (2 Tabs)**: Divide the drawer into two dedicated tabs via a slider segmented control:
   - **`[ ✦ Tiến trình xử lý ]`**: Focused exclusively on the 3 core execution steps using the approved Option A connected timeline.
   - **`[ 🧠 Bộ nhớ ]`**: Dedicated view for Episodic Memory and Working Memory sync status.
3. **Option A Connected 3-Step Timeline**:
   - **1. Hiểu yêu cầu**: Concise routing chip (`RAG · Truy xuất tài liệu` vs `Direct · Hội thoại trực tiếp`).
   - **2. Tìm thông tin liên quan**: Chunk count + document file chips (`📄 cap_lai_cccd.pdf`), or `Không yêu cầu truy xuất tài liệu` for direct turns.
   - **3. Tổng hợp câu trả lời**: Model badge, reasoning duration, and formatted CoT with copy button — the single place where reasoning UX is invested in.

## User Stories

1. As a chat user, I want the answer bubble to contain the answer and its evidence only, so that nothing competes with the response I asked for.
2. As a technical user, I want to open the "Chi tiết xử lý" drawer and switch between "Tiến trình xử lý" and "Bộ nhớ" using a segmented slider, so that I can inspect pipeline steps and memory state separately.
3. As a technical user, I want the "Tiến trình xử lý" tab to display a clean 3-step connected timeline (Hiểu yêu cầu, Tìm thông tin liên quan, Tổng hợp câu trả lời), so that I can review the execution path without visual clutter.
4. As a technical user, I want Step 1 to clearly show whether the request was routed to Direct Chat or RAG Retrieval, so that I understand how my prompt was classified.
5. As a technical user, I want Step 2 to display the count of retrieved chunks and the exact document filenames, so that I know which files grounded the response.
6. As a technical user, I want Step 2 to gracefully state "Không yêu cầu truy xuất tài liệu" when asking general direct questions, so that I am confident no irrelevant files were searched.
7. As a technical user, I want Step 3 to show the model name, execution duration, and the full CoT reasoning trace, so that I have complete visibility into model inference.
8. As a technical user, I want a copy button on the CoT block in Step 3, so that I can move the model's reasoning into my own notes.
9. As a user operating in Fast mode, I want Step 3 to state that thinking was disabled for low latency, so that I understand why no CoT is present.
10. As a keyboard user, I want full accessibility across the drawer tabs with proper ARIA attributes, so that I can navigate and toggle sections using standard keys.

## Implementation Decisions

- **No inline reasoning in the chat stream**:
  - `ChatStreamView` renders no reasoning container. The former `InlineReasoningCard` component and its live stopwatch were removed rather than hidden, so there is one owner of reasoning presentation.
  - `ChatMessage.executionTrace` still travels with the message; it is consumed by `ExecutionTraceDrawer` only.
- **Drawer Segmented Control**:
  - Uses an accessible slider-style segmented button group with `role="tablist"` and `role="tab"`.
  - State `activeTab: 'process' | 'memory'` controls view rendering within `ExecutionTraceDrawer`.
- **Option A Connected 3-Step Timeline**:
  - Uses a single continuous vertical connector line (`w-px bg-[#413b34]`) with circular status node indicators.
  - Step 1 (`understanding_request`): Renders route outcome badge (`RAG` vs `Direct`).
  - Step 2 (`searching_relevant_information`): Renders chunk count and document filename pills.
  - Step 3 (`preparing_response` / `preparing_action_plan`): Renders the model badge, the reasoning duration derived from the activity timestamps, the CoT trace, a truncation warning when applicable, and the copy button.
  - Reasoning duration formatting is shared through `reasoningDuration.ts` (`formatSecondsVi`, `spanMilliseconds`).
- **Memory Tab Structure**:
  - Renders a memory status card displaying Episodic and Working memory synchronization health (`Sẵn sàng & Đồng bộ` vs `Một phần suy giảm`).
- **Zero Backend Contract Changes**:
  - Leverages the existing `ChatExecutionTrace`, `ChatActivity`, and SSE streaming event payload architecture without breaking SQLite or HTTP wire schemas.

## Testing Decisions

- **Behavior-Driven Testing**:
  - Test all interactions via external user actions (`fireEvent.click`) and assert visible DOM states / ARIA attributes (`aria-expanded`, `aria-selected`).
  - Do not assert on private component state or internal timer intervals.
- **Modules Tested**:
  - `ExecutionTraceDrawer.test.tsx`: Tests tab switching between `process` and `memory`, 3-step connected timeline rendering, RAG document pill presentation, reasoning duration, and copy-to-clipboard behavior.
  - `ChatStreamView.test.tsx`: Asserts that no reasoning container is rendered in the assistant bubble.
- **Prior Art**:
  - Follows testing patterns established in `frontend/src/dashboard/components/AgentActivityTimeline.test.tsx`.

## Out of Scope

- Any reasoning affordance inside the chat stream (inline accordion, live stopwatch pill, streamed thinking text in the bubble).
- Token-by-token streaming of raw reasoning tokens before completion (provider transport currently yields the final trace upon response completion). Tracked separately as a follow-up.
- Modifying SQLite database schemas or server-side memory storage tables.
- Editing or modifying user document indexes.

## Further Notes

- All colors, border radii, and typographic styles follow the existing Cowork Agent dark palette (`#1e1d1a`, `#24211d`, `#383531`, `#d97757`, `#e8a78f`).
