const vi = typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('vi');

const errors: Record<string, [string, string]> = {
  ocr_not_configured: ['PDF chỉ chứa ảnh; OCR hiện chưa khả dụng.', 'This PDF contains scanned images; OCR is unavailable.'],
  ocr_unavailable: ['PDF chỉ chứa ảnh; OCR hiện chưa khả dụng.', 'This PDF contains scanned images; OCR is unavailable.'],
  source_metadata_mismatch: ['Tệp tải lên không khớp thông tin đã đăng ký.', 'The uploaded file does not match its registered metadata.'],
  source_media_type_mismatch: ['Định dạng thực của tệp không đúng.', 'The file contents do not match the declared type.'],
  unsupported_media_type: ['Chỉ hỗ trợ tệp PDF và DOCX.', 'Only PDF and DOCX files are supported.'],
  empty_extraction: ['Không tìm thấy nội dung chữ trong tài liệu.', 'No readable text was found in the document.'],
  page_limit_exceeded: ['Tài liệu vượt quá giới hạn số trang.', 'The document exceeds the page limit.'],
  ingestion_failed: ['Không thể xử lý tài liệu.', 'The document could not be processed.'],
};

export function documentText(key: string): string {
  const values: Record<string, [string, string]> = {
    title: ['Tài liệu dự án', 'Project documents'],
    upload: ['Tải PDF hoặc DOCX', 'Upload PDF or DOCX'],
    processing: ['Đang xử lý — chưa sẵn sàng', 'Processing — not ready yet'],
    deleting: ['Đang xóa', 'Deleting'],
    empty: ['Dự án chưa có tài liệu.', 'No documents in this project.'],
    listUnavailable: ['Không thể tải danh sách tài liệu.', 'Document list unavailable.'],
    uploadFailed: ['Tải tài liệu thất bại.', 'Upload failed.'],
    processingTimeout: ['Xử lý quá thời gian. Bạn có thể xóa tài liệu và thử lại.', 'Processing timed out. You can delete the document and try again.'],
    pages: ['trang', 'pages'],
    chunks: ['đoạn', 'chunks'],
    expires: ['Hết hạn', 'Expires'],
    retention: ['25 MiB/tệp · lưu 30 ngày', '25 MiB/file · retained for 30 days'],
  };
  const pair = values[key];
  return pair ? pair[vi ? 0 : 1] : key;
}

export function documentLocale(): string {
  return vi ? 'vi-VN' : 'en-US';
}

export function documentError(code: string): string {
  const pair = errors[code];
  return pair
    ? pair[vi ? 0 : 1]
    : (vi ? 'Không thể xử lý tài liệu.' : 'Document processing failed.');
}
