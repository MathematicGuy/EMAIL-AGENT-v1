import React, { useEffect, useState } from 'react';

interface NewProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (name: string) => void;
}

export const NewProjectModal: React.FC<NewProjectModalProps> = ({ isOpen, onClose, onCreate }) => {
  const [name, setName] = useState('');
  useEffect(() => {
    if (isOpen) queueMicrotask(() => setName(''));
  }, [isOpen]);
  if (!isOpen) return null;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    onCreate(name.trim());
    setName('');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-label="Tạo dự án">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-zinc-700 bg-[#24221f] p-5 shadow-2xl">
        <h2 className="text-base font-semibold text-zinc-100">Tạo dự án mới</h2>
        <p className="mt-1 text-sm text-zinc-400">Tổ chức các cuộc trò chuyện và bộ nhớ theo từng dự án riêng biệt.</p>
        <label className="mt-5 block text-sm text-zinc-300" htmlFor="project-name">Tên dự án</label>
        <input id="project-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Ví dụ: Chatbot tuyển dụng" className="mt-2 w-full rounded-lg border border-zinc-700 bg-[#1b1a18] px-3 py-2 text-sm text-zinc-100 outline-none focus:border-[#d97757]" />
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={() => { setName(''); onClose(); }} className="rounded-lg px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-700/50">Hủy</button>
          <button type="submit" disabled={!name.trim()} className="rounded-lg bg-[#d97757] px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">Tạo dự án</button>
        </div>
      </form>
    </div>
  );
};
