# Design System: Let's noodle AI Interface

## Overview

An ultra-refined, warm dark-mode AI Chat workspace inspired by modern AI interfaces ("Let's noodle"). Designed with pixel-perfect fidelity to `images/fe.png`, featuring a dark charcoal canvas, warm serif branding typography, floating input card, interactive sidebar drawer, model switcher, and rich AI conversation capabilities.

---

## 🎨 Color Palette

### Base Surfaces

- **Canvas / Background**: `#1c1b18` (Warm Deep Charcoal)
- **Secondary Surface / Card**: `#292825` (Soft Charcoal Card)
- **Hover Surface**: `#34322f` (Elevated Charcoal)
- **Border / Divider**: `#33312e` (Subtle Hairline Border)
- **Active State**: `#3d3a36`

### Accent Colors

- **Noodle Coral / Starburst**: `#d97757` (Warm Terracotta Coral)
- **Status Blue (Download Dot)**: `#3b82f6` (Vibrant Electric Blue)
- **Text Primary**: `#f3f2ef` (Off-white / Soft Pearl)
- **Text Muted / Placeholder**: `#949089` (Muted Warm Grey)
- **Text Subdued**: `#6c6862` (Deep Muted)

---

## Typography & Fonts

- **Display Serif**: `Newsreader` / `Lora` / `Playfair Display` (Used for logo title & greetings with full Vietnamese diacritics support)
- **UI Sans**: `Geist` / `Inter` / System Sans (Used for workspace controls, menus, inputs)
- **Code Mono**: `Geist Mono` / `JetBrains Mono` (Used for code blocks and technical data)

---

## 🧩 Component Architecture

### 1. Left Navigation Rail & Sidebar

- **Top Section**:
  - Panel expand/collapse toggle
  - Divider
  - `+` New Chat action button
  - Chat history icon (Speech bubble)
  - Projects / Library icon (Cylinder stack)
  - Workflows / Diagram icon (Network nodes)
  - Code icon (`</>`)
  - Tools / Artifacts icon (Briefcase)
- **Middle Section**:
  - Theme / Styling palette icon
- **Bottom Section**:
  - Download desktop app icon (with blue status indicator dot)
  - User profile avatar (`DM` circle)
- **Drawer Overlay/Slide**:
  - Slides out on icon click to display detailed chat history, recent projects, workflow templates, code snippets, or user settings.

### 2. Header Bar

- **Center Pill**: `Free plan · Upgrade` badge with rounded border, interactive to open Plan Upgrade modal.
- **Top Right**: Ghost / Assistant settings icon button.

### 3. Hero & Central Workspace Stage

- **Title Block**:
  - Warm Terracotta Starburst Icon (`✳` / custom 8-spoke star)
  - "Let's noodle" in elegant display serif typography
- **Floating Input Card**:
  - Dark rounded container (`rounded-2xl`, `#292825` background, `#383532` border on hover/focus)
  - Multi-line dynamic auto-expanding textarea with placeholder: "How can I help you today?"
  - **Bottom Control Strip**:
    - **Left**: `+` Attachment button (triggers file/image/code upload popover)
    - **Right**:
      - Model selector pill: `Sonnet 5 Medium v` (dropdown for switching models like Sonnet 5, Opus 4.1, Haiku 4.5)
      - Microphone icon button (triggers audio input / speech recording mode)
      - Voice visualizer waveform button (triggers ambient live voice chat overlay)

### 4. Interactive AI Chat & Features

- **Message Feed**: When user sends a query, seamlessly transitions from home hero to active conversation mode.
- **AI Streaming Response**: Realistic simulated streaming output with markdown, code syntax highlighting, copy-code button, and action buttons (Retry, Like, Dislike, Copy).
- **Modals & Overlays**:
  - **Model Selector Dropdown**: Switch between AI models with speed, intelligence, and context window metrics.
  - **Upgrade Plan Modal**: Pricing tiers (Free vs Pro vs Team) with feature checklists.
  - **Voice Mode Modal**: Animated waveform audio visualizer dialog.
  - **Attachment Popover**: Options for uploading images, documents, or pasting code.
  - **Theme Customizer**: Switch between Warm Charcoal, Midnight Black, Obsidian, and Emerald Dark.
  - **User Settings Modal**: Manage preferences, initial avatar `DM`, and account settings.

---

### 5. Agentic execution feedback

- The main chat surface uses natural status text such as “Đang đọc tài liệu…”,
  “Đang tổng hợp nội dung…” and “Đã hoàn thành”.
- Task IDs, plan versions, capability IDs and raw step states live inside a
  collapsed “Chi tiết các bước” disclosure.
- Clarification messages may render compact quick-action chips. Selecting one
  submits a reply to the waiting turn; it does not create an unrelated task.
- Technical identifiers remain available for support and audit, but are never
  the primary progress message.

### Honest grounding indicator

Artifact preview hiển thị một trạng thái do server cung cấp, không suy luận từ UI:

- Xanh: `Grounded Result`, kèm marker `[1]`, `[2]` và danh sách nguồn.
- Vàng: `Bản thảo sơ bộ dựa trên yêu cầu (Chưa có tài liệu đối soát đính kèm)`, không hiển thị citation giả.

Marker trong preview cuộn tới entry tương ứng trong danh sách nguồn. Indicator phải xuất hiện ở chat artifact card, Cowork preview, Work Intake detail và trong Markdown tải xuống.

## 📱 Responsive & Motion Specifications

- **Transitions**: Smooth 150ms-200ms ease-in-out on hover, focus, and drawer slide.
- **Mobile Adaptability**: Sidebar collapses into bottom or side sheet for smaller viewports.
- **Focus Rings**: Subtle `#d97757` or `#555` outline without ugly default browser rings.
