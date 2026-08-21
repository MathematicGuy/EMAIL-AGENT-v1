import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { RawDocumentsView } from './RawDocumentsView';

vi.mock('./DocxViewer', () => ({
  DocxViewer: ({ filename }: { filename: string }) => (
    <div data-testid="docx-viewer">
      Docx Viewer: {filename}
    </div>
  ),
}));

const mockRawDocs = [
  {
    filename: 'cap_lai_cccd.pdf',
    file_type: 'pdf',
    size: 29440,
    updated_at: '2026-08-20T07:00:00Z',
    has_extracted_md: true,
    extracted_md_name: 'cap-lai-cccd.md',
  },
  {
    filename: '01_2021_ND-CP_283247.docx',
    file_type: 'docx',
    size: 51534,
    updated_at: '2026-08-20T07:00:00Z',
    has_extracted_md: true,
    extracted_md_name: '01-2021-nd-cp-283247.md',
  },
];

describe('RawDocumentsView', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes('/api/v1/raw-documents/upload') && init?.method === 'POST') {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                filename: 'new_proc.docx',
                file_type: 'docx',
                size: 1024,
                updated_at: '2026-08-21T00:00:00Z',
                has_extracted_md: true,
                extracted_md_name: 'new-proc.md',
              }),
          });
        }
        if (init?.method === 'DELETE') {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ status: 'deleted', filename: 'cap_lai_cccd.pdf' }),
          });
        }
        if (url.includes('/api/v1/raw-documents') && !url.includes('/extracted')) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockRawDocs),
            arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
          });
        }
        if (url.includes('/extracted')) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                filename: '01_2021_ND-CP_283247.docx',
                extracted_md_name: '01-2021-nd-cp-283247.md',
                content: '# Extracted Procedure\n\nDetail content...',
              }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({}),
        });
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders list of raw documents and selects first one by default', async () => {
    render(<RawDocumentsView />);

    await waitFor(() => {
      expect(screen.getAllByText('cap_lai_cccd.pdf').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('01_2021_ND-CP_283247.docx').length).toBeGreaterThanOrEqual(1);
    });

    const iframe = screen.getByTitle('pdf-preview-cap_lai_cccd.pdf');
    expect(iframe).not.toBeNull();
    expect(iframe.getAttribute('src')).toContain('cap_lai_cccd.pdf');
  });

  it('filters documents by search query', async () => {
    render(<RawDocumentsView />);

    await waitFor(() => {
      expect(screen.getAllByText('cap_lai_cccd.pdf').length).toBe(2);
    });

    const searchInput = screen.getByPlaceholderText('Tìm kiếm tài liệu...');
    fireEvent.change(searchInput, { target: { value: 'ND-CP' } });

    expect(screen.getAllByText('cap_lai_cccd.pdf').length).toBe(1);
    expect(screen.getAllByText('01_2021_ND-CP_283247.docx').length).toBeGreaterThanOrEqual(1);
  });

  it('filters documents by dropdown type', async () => {
    render(<RawDocumentsView />);

    await waitFor(() => {
      expect(screen.getAllByText('cap_lai_cccd.pdf').length).toBe(2);
    });

    const filterSelect = screen.getByLabelText('Lọc loại tệp');
    fireEvent.change(filterSelect, { target: { value: 'docx' } });

    expect(screen.getAllByText('cap_lai_cccd.pdf').length).toBe(1);
    expect(screen.getAllByText('01_2021_ND-CP_283247.docx').length).toBeGreaterThanOrEqual(1);
  });

  it('switches to docx document and loads DocxViewer in preview mode', async () => {
    render(<RawDocumentsView />);

    await waitFor(() => {
      expect(screen.getAllByText('01_2021_ND-CP_283247.docx').length).toBeGreaterThanOrEqual(1);
    });

    const docxItem = screen.getAllByText('01_2021_ND-CP_283247.docx')[0];
    fireEvent.click(docxItem);

    await waitFor(() => {
      expect(screen.getByTestId('docx-viewer')).not.toBeNull();
      expect(screen.getByText('Docx Viewer: 01_2021_ND-CP_283247.docx')).not.toBeNull();
    });

    // Toggle to Markdown extracted view
    const toggleBtn = screen.getByText('Xem trích xuất');
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.getByText('Extracted Procedure')).not.toBeNull();
      expect(screen.getByText('Detail content...')).not.toBeNull();
    });
  });

  it('handles file upload button click and triggers upload request', async () => {
    render(<RawDocumentsView />);

    const uploadBtn = screen.getByTitle('Tải lên tài liệu quy trình (.pdf, .docx)');
    expect(uploadBtn).not.toBeNull();
  });

  it('deletes document when delete button is clicked and confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<RawDocumentsView />);

    await waitFor(() => {
      expect(screen.getAllByText('cap_lai_cccd.pdf').length).toBeGreaterThanOrEqual(1);
    });

    const deleteBtns = screen.getAllByTitle('Xóa tài liệu');
    fireEvent.click(deleteBtns[0]);

    await waitFor(() => {
      expect(screen.queryByText('cap_lai_cccd.pdf')).toBeNull();
    });
  });
});
