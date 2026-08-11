import type { RecentChat, ModelOption, CoworkTask } from '../types';

export const RECENT_CHATS: RecentChat[] = [
  { id: '1', title: 'Mac directory restructuring', date: 'Hôm nay' },
  { id: '2', title: 'Trích xuất đề bài từ trang web JavaScript', date: 'Hôm nay' },
  { id: '3', title: 'Viết script video demo cho sản phẩm N...', date: 'Hôm qua' },
  { id: '4', title: 'Tạo slide pitch deck cho Hackathon', date: 'Hôm qua' },
  { id: '5', title: 'Viết prompt thiết kế lại giao diện app', date: '3 ngày trước' },
  { id: '6', title: 'Slide bài giảng hoá 10 bài 4 dạng HTML', date: '3 ngày trước' },
  { id: '7', title: 'Bổ sung nội dung hợp lý', date: 'Tuần này' },
  { id: '8', title: 'Empty message', date: 'Tuần này' },
  { id: '9', title: 'Untitled', date: 'Tuần trước' },
  { id: '10', title: 'Your first chat with Claude', date: 'Tuần trước' }
];

export const AVAILABLE_MODELS: ModelOption[] = [
  {
    id: 'gemini-3.5-flash-lite',
    name: 'Gemini 3.5 Flash Lite',
    version: '3.5',
    description: 'Google Gemini 3.5 Flash Lite.',
    badge: 'Default'
  },
  {
    id: 'gemini-3.6-flash-lite',
    name: 'Gemini 3.6 Flash Lite',
    version: '3.6',
    description: 'Google Gemini 3.6 Flash Lite.',
    badge: 'Gemini'
  },
  {
    id: 'gemini-3.5-flash',
    name: 'Gemini 3.5 Flash',
    version: '3.5',
    description: 'Google Gemini 3.5 Flash.',
    badge: 'Gemini'
  },
  {
    id: 'gemini-3.6-flash',
    name: 'Gemini 3.6 Flash',
    version: '3.6',
    description: 'Google Gemini 3.6 Flash.',
    badge: 'Gemini'
  },
  {
    id: 'deepseek-openrouter',
    name: 'DeepSeek · OpenRouter',
    version: 'OpenRouter',
    description: 'DeepSeek qua OpenRouter, sử dụng OPENROUTER_MODEL trên backend.',
    badge: 'OpenRouter'
  },
  {
    id: 'deepseek-nvidia',
    name: 'DeepSeek · NVIDIA',
    version: 'NVIDIA',
    description: 'DeepSeek qua NVIDIA NIM, sử dụng NVIDIA_MODEL trên backend.',
    badge: 'NVIDIA'
  }
];

export const IDEAS_FOR_YOU = [
  { id: 'idea-1', icon: '🌤', title: 'Tạo tài liệu', prompt: 'Tạo một tài liệu kế hoạch dự án rõ ràng, bao gồm mục tiêu, phạm vi, các bước thực hiện và tiêu chí hoàn thành.' },
  { id: 'idea-2', icon: '💬', title: 'Tạo báo cáo', prompt: 'Tạo một báo cáo tiến độ dự án ngắn gọn, bao gồm tóm tắt, kết quả, rủi ro và đề xuất tiếp theo.' },
  { id: 'idea-3', icon: '📝', title: 'Customize Cowork for me', prompt: 'Guide me through tailoring Claude Cowork workflows for my specific team needs.' }
];

export const COWORK_SAMPLE_TASKS: CoworkTask[] = [
  {
    id: 'task-cowork-1',
    title: 'Xây Dựng Kế Hoạch & PRD Cho Tính Năng Claude Cowork Web',
    goal: 'Biến yêu cầu công việc của người dùng thành Kế hoạch thực thi (ExecPlan), phân tích 3 nguồn tài liệu đầu vào, tự động sinh Artifact tài liệu PRD hoàn chỉnh và yêu cầu phê duyệt trước khi xuất bản.',
    status: 'waiting_approval',
    progress: 80,
    contextSources: [
      { id: 's1', name: 'REPO_ARCHITECTURE.md', type: 'code', size: '14.5 KB' },
      { id: 's2', name: 'DESIGN.md', type: 'docx', size: '4.2 KB' },
      { id: 's3', name: 'research-feature-v3.md', type: 'pdf', size: '8.7 KB' }
    ],
    steps: [
      { id: 'st1', name: 'Work Intake & Clarify Scope', status: 'completed', details: 'Xác định mục tiêu công việc, acceptance criteria và ranh giới hệ thống.' },
      { id: 'st2', name: 'Context Scan & Intelligence Ingest', status: 'completed', details: 'Quét và trích xuất dữ liệu từ REPO_ARCHITECTURE.md, DESIGN.md & spec.' },
      { id: 'st3', name: 'Generate Execution Plan (ExecPlan)', status: 'completed', details: 'Lập danh sách 5 bước tự động hóa có kiểm soát.' },
      { id: 'st4', name: 'Artifact Live Generation', status: 'completed', details: 'Tạo tài liệu PRD chuẩn format markdown side-by-side.' },
      { id: 'st5', name: 'Human-in-the-loop Approval & Publish', status: 'waiting_approval', details: 'Yêu cầu người dùng xác nhận phê duyệt trước khi commit mã nguồn.' }
    ],
    artifact: {
      id: 'art-1',
      title: 'Tài Liệu PRD: Claude Cowork Web Workspace',
      type: 'markdown',
      version: 'v1.0',
      content: `# PRD: Claude Cowork Web Workspace

## 1. Tóm tắt mục tiêu (Executive Summary)
Hệ thống **Claude Cowork Web Workspace** biến các yêu cầu phức tạp từ người dùng thành quy trình thực thi tự động có kiểm soát:
- **Human-in-the-loop**: Người dùng xác định mục tiêu và phê duyệt trước các hành động nhạy cảm.
- **AI Coworker**: Agent khảo sát nguồn dữ liệu, lập kế hoạch 5 bước, sinh Artifact và để lại minh chứng (provenance).

---

## 2. Các thành phần chính (Core Architecture)

### 📌 A. Work Intake & Planning
1. Khảo sát phạm vi yêu cầu (Scope Clarification).
2. Phát hiện thông tin còn thiếu và đề xuất câu hỏi làm rõ.
3. Sinh bản đồ kế hoạch ExecPlan với trạng thái thời gian thực.

### 📄 B. Live Artifact Preview & Side-by-Side Stage
- Hiển thị trực tiếp sản phẩm (Báo cáo, Đồ án PRD, Sơ đồ Mermaid, Code Snippets) ngay bên cạnh khung chat.
- Hỗ trợ nút **Approve & Commit**, **Export PDF**, **Edit Content**.

---

## 3. Quy trình thực thi (Workflow Lifecycle)
\`\`\`txt
Work Intake ➔ Context Ingest ➔ Plan Generation ➔ Live Preview ➔ Approval ➔ Completed
\`\`\`

## 4. Tiêu chí chấp nhận (Acceptance Criteria)
- [x] Giao diện chia đôi màn hình (Split-screen Cowork Mode).
- [x] Tiến trình công việc dạng Progress Bar real-time.
- [x] Hỗ trợ phê duyệt Human-in-the-loop mượt mà.`
    }
  }
];

export const DEMO_STREAMING_RESPONSES = [
  {
    topic: 'default',
    text: `Dưới đây là thiết kế chi tiết cho hệ thống AI Assistant với đầy đủ streaming text animation và responsive layout theo đúng đặc tả:

1. **Thanh Taskbar / Sidebar**:
   - Chế độ Thu gọn (v1.png): Rộng 52px, chứa icon thu gọn ở nút đầu tiên. Click nút này để mở rộng.
   - Chế độ Mở rộng (v2.png): Rộng 260px, hiển thị đầy đủ danh sách Chats, Projects, Artifacts, Code, Customize và danh sách Recents.

2. **Khung Chat & Streaming Animation**:
   - Phản hồi dạng real-time token streaming với hiệu ứng con trỏ nhấp nháy ▌.
   - Hỗ trợ định dạng Markdown, Code block highlight, nút Copy và công cụ thao tác nhanh.

Dưới đây là đoạn mã ví dụ triển khai custom hook cho streaming animation trong React:

\`\`\`typescript
import { useState, useEffect, useRef } from 'react';

export function useStreamingText(fullText: string, speedMs: number = 20) {
  const [displayedText, setDisplayedText] = useState('');
  const [isDone, setIsDone] = useState(false);
  const indexRef = useRef(0);

  useEffect(() => {
    setDisplayedText('');
    setIsDone(false);
    indexRef.current = 0;

    const timer = setInterval(() => {
      if (indexRef.current < fullText.length) {
        setDisplayedText(prev => prev + fullText.charAt(indexRef.current));
        indexRef.current += 1;
      } else {
        setIsDone(true);
        clearInterval(timer);
      }
    }, speedMs);

    return () => clearInterval(timer);
  }, [fullText, speedMs]);

  return { displayedText, isDone };
}
\`\`\`

Bạn có thể tương tác với các nút bấm trên giao diện để thử nghiệm tính năng!`
  }
];
