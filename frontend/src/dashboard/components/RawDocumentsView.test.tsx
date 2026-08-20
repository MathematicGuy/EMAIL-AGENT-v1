import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { RawDocumentsView } from './RawDocumentsView';

vi.mock('@onlyoffice/document-editor-react', () => ({
  DocumentEditor: ({
    config,
    id,
  }: {
    config?: { document?: { title?: string } };
    id: string;
  }) => (
    <div data-testid="onlyoffice-editor" id={id}>
      OnlyOffice Editor: {config?.document?.title}
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
      vi.fn().mockImplementation((url: string) => {
        if (url.includes('/api/v1/raw-documents') && !url.includes('/extracted') && !url.includes('/onlyoffice-config')) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockRawDocs),
          });
        }
        if (url.includes('/onlyoffice-config')) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                document: {
                  fileType: 'docx',
                  key: 'mock_key_123',
                  title: '01_2021_ND-CP_283247.docx',
                  url: 'http://test/api/v1/raw-documents/01_2021_ND-CP_283247.docx',
                },
                documentType: 'word',
                editorConfig: {
                  callbackUrl: 'http://test/callback',
                },
                documentServerUrl: 'http://localhost:8080',
              }),
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

    const iframe = screen.getByTitle('cap_lai_cccd.pdf');
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

  it('switches to docx document and loads OnlyOffice editor in preview mode', async () => {
    render(<RawDocumentsView />);

    await waitFor(() => {
      expect(screen.getAllByText('01_2021_ND-CP_283247.docx').length).toBeGreaterThanOrEqual(1);
    });

    const docxItem = screen.getAllByText('01_2021_ND-CP_283247.docx')[0];
    fireEvent.click(docxItem);

    await waitFor(() => {
      expect(screen.getByTestId('onlyoffice-editor')).not.toBeNull();
      expect(screen.getByText('OnlyOffice Editor: 01_2021_ND-CP_283247.docx')).not.toBeNull();
    });

    // Toggle to Markdown extracted view
    const toggleBtn = screen.getByText('Xem trích xuất');
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.getByText('Extracted Procedure')).not.toBeNull();
      expect(screen.getByText('Detail content...')).not.toBeNull();
    });
  });
});
