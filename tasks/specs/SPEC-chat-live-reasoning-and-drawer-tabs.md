# Spec: Chat Live Reasoning & Execution Trace Drawer Tabs

## Problem Statement

Users interacting with reasoning models (Xiaomi MiMo and Mistral AI) currently experience opaque pauses during inference without real-time visual feedback on thinking progress. Furthermore, the "Chi tiết xử lý" (Execution Trace) drawer conflates operational processing steps with memory state in a single vertical list containing verbose boilerplate text, cluttering the technical inspection experience.

## Solution

1. **Inline ChatGPT-Style Live Reasoning**: Directly inside the chat stream message bubble, render an inline collapsible reasoning container with a live stopwatch counter (`🧠 Đang suy luận... 3.8s`) during streaming that automatically collapses to a compact pill (`✦ Đã suy luận trong 3,8s · mimo-v2.5-pro [▼]`) upon completion.
2. **Drawer Segmented Control (2 Tabs)**: Divide the "Chi tiết xử lý" Drawer into two dedicated tabs via a slider segmented control:
   - **`[ ✦ Tiến trình xử lý ]`**: Focused exclusively on the 3 core execution steps using the approved Option A connected timeline.
   - **`[ 🧠 Bộ nhớ ]`**: Dedicated view for Episodic Memory, Working Memory, and User Profile sync status.
3. **Option A Connected 3-Step Timeline**:
   - **1. Hiểu yêu cầu**: Concise routing chip (`RAG · Truy xuất tài liệu` vs `Hội thoại trực tiếp · Direct`).
   - **2. Tìm thông tin liên quan**: Chunks count badge + document file chips (`📄 cap_lai_cccd.pdf`).
   - **3. Tổng hợp câu trả lời**: Model badge, reasoning time, and formatted Chain of Thought (CoT) with copy button.

## User Stories

1. As a chat user, I want to see a live thinking timer and status in the message stream while the AI is generating, so that I know the model is actively working on my complex request.
2. As a chat user, I want the thinking block to automatically collapse into a compact summary badge when generation finishes, so that my focus stays on the final answer.
3. As a chat user, I want to click the collapsed thinking badge at any time to expand and read the model's full Chain of Thought (CoT), so that I can audit its step-by-step logic.
4. As a chat user, I want a copy button inside the reasoning block, so that I can easily copy the model's reasoning trace to my clipboard.
5. As a technical user, I want to open the "Chi tiết xử lý" drawer and switch between "Tiến trình xử lý" and "Bộ nhớ" using a segmented slider, so that I can inspect pipeline steps and memory state separately.
6. As a technical user, I want the "Tiến trình xử lý" tab to display a clean 3-step connected timeline (Hiểu yêu cầu, Tìm thông tin liên quan, Tổng hợp câu trả lời), so that I can review the execution path without visual clutter.
7. As a technical user, I want Step 1 to clearly show whether the request was routed to Direct Chat or RAG Retrieval, so that I understand how my prompt was classified.
8. As a technical user, I want Step 2 to display the count of retrieved chunks and the exact document filenames, so that I know which files grounded the response.
9. As a technical user, I want Step 2 to gracefully state "Không yêu cầu truy xuất tài liệu" when asking general direct questions, so that I am confident no irrelevant files were searched.
10. As a technical user, I want Step 3 to show the model name, execution duration, and full CoT reasoning trace, so that I have complete visibility into model inference.
11. As a user operating in Fast mode, I want the reasoning containers to clearly state that thinking was disabled for low latency, so that I understand why no CoT is present.
12. As a keyboard user, I want full accessibility across the inline thinking accordion and drawer tabs with proper ARIA attributes, so that I can navigate and toggle sections using standard keys.

## Implementation Decisions

- **Inline Reasoning Component (`InlineReasoningCard`)**:
  - Encapsulated as a standalone presentational component inside the assistant message bubble above the formatted markdown.
  - Maintains local expansion state: initialized to `true` while generating, auto-transitions to `false` (collapsed) on generation completion.
  - Houses a 100ms interval timer active strictly during `generationStatus === 'generating'` to track perceived inference duration.
- **Drawer Segmented Control**:
  - Uses an accessible slider-style segmented button group with `role="tablist"` and `role="tab"`.
  - State `activeTab: 'process' | 'memory'` controls view rendering within `ExecutionTraceDrawer`.
- **Option A Connected 3-Step Timeline**:
  - Uses a single continuous vertical connector line (`w-px bg-[#413b34]`) with circular status node indicators.
  - Step 1 (`understanding_request`): Renders route outcome badge (`RAG` vs `Direct`).
  - Step 2 (`searching_relevant_information`): Renders chunk count and document filename pills.
  - Step 3 (`preparing_response` / `preparing_action_plan`): Renders the model CoT trace, truncation warning if applicable, and copy button.
- **Memory Tab Structure**:
  - Renders memory status card displaying Episodic Memory and Working Memory synchronization health (`Sẵn sàng & Đồng bộ` vs `Một phần suy giảm`).
- **Zero Backend Contract Changes**:
  - Leverages the existing `ChatExecutionTrace`, `ChatActivity`, and SSE streaming event payload architecture without breaking SQLite or HTTP wire schemas.

## Testing Decisions

- **Behavior-Driven Testing**:
  - Test all interactions via external user actions (`fireEvent.click`) and assert visible DOM states / ARIA attributes (`aria-expanded`, `aria-selected`).
  - Do not assert on private component state or internal timer intervals.
- **Modules Tested**:
  - `InlineReasoningCard.test.tsx`: Tests live generating timer, auto-collapse, user expand/collapse toggle, and copy-to-clipboard behavior.
  - `ExecutionTraceDrawer.test.tsx`: Tests tab switching between `process` and `memory`, 3-step connected timeline rendering, and RAG document pill presentation.
  - `ChatStreamView.test.tsx`: Tests integration of `InlineReasoningCard` within assistant message stream.
- **Prior Art**:
  - Follows testing patterns established in `frontend/src/dashboard/components/ExecutionTraceDrawer.test.tsx` and `AgentActivityTimeline.test.tsx`.

## Out of Scope

- Modifying SQLite database schemas or server-side memory storage tables.
- Token-by-token streaming of raw reasoning tokens before completion (provider transport yields final trace upon response completion).
- Editing or modifying user document indexes.

## Further Notes

- All colors, border radii, and typographic styles follow the existing Cowork Agent dark palette (`#1e1d1a`, `#24211d`, `#383531`, `#d97757`, `#e8a78f`).
