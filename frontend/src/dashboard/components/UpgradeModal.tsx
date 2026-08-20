import React from 'react';
import { X, Check } from 'lucide-react';

interface UpgradeModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const UpgradeModal: React.FC<UpgradeModalProps> = ({ isOpen, onClose }) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-md flex items-center justify-center p-4">
      <div className="bg-[#242320] border border-[#3a3834] rounded-2xl w-full max-w-2xl p-6 shadow-2xl space-y-6 text-zinc-100 animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#35332f] pb-4">
          <div className="flex items-center gap-3">
            <img src="/images/f-cowork-logo.svg" alt="F-Cowork Logo" className="h-8 object-contain" />
            <div>
              <h2 className="font-semibold text-lg text-zinc-100">Nâng cấp gói dịch vụ</h2>
              <p className="text-xs text-zinc-400">Mở khóa giới hạn cao hơn, mô hình suy luận mạnh mẽ và công cụ nâng cao.</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-zinc-400 hover:text-zinc-100 hover:bg-[#32302c] rounded-md transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Pricing Cards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Free Plan Card */}
          <div className="p-4 rounded-xl bg-[#1d1c1a] border border-[#33312d] flex flex-col justify-between">
            <div>
              <div className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">Gói hiện tại</div>
              <div className="text-xl font-bold text-zinc-100 mb-1">Miễn phí</div>
              <div className="text-2xl font-semibold text-zinc-300 mb-4">0đ <span className="text-xs font-normal text-zinc-500">/ tháng</span></div>

              <ul className="space-y-2 text-xs text-zinc-300">
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-zinc-500" />
                  <span>Truy cập mô hình Gemini cơ bản</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-zinc-500" />
                  <span>Tốc độ phản hồi tiêu chuẩn</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-zinc-500" />
                  <span>Cửa sổ ngữ cảnh lịch sử cơ bản</span>
                </li>
              </ul>
            </div>

            <button
              disabled
              className="mt-6 w-full py-2 bg-[#2c2a26] text-zinc-500 font-medium text-xs rounded-lg cursor-default"
            >
              Gói đang sử dụng
            </button>
          </div>

          {/* Pro Plan Card */}
          <div className="p-4 rounded-xl bg-[#2b2925] border-2 border-[#d97757] flex flex-col justify-between relative shadow-lg">
            <span className="absolute -top-3 right-4 bg-[#d97757] text-white text-[10px] font-bold px-2.5 py-0.5 rounded-full uppercase tracking-wider">
              Khuyên dùng
            </span>
            <div>
              <div className="text-xs font-semibold text-[#d97757] uppercase tracking-wider mb-1">Gói Pro</div>
              <div className="text-xl font-bold text-zinc-100 mb-1">F-Cowork Pro</div>
              <div className="text-2xl font-semibold text-zinc-100 mb-4">20$ <span className="text-xs font-normal text-zinc-400">/ tháng</span></div>

              <ul className="space-y-2 text-xs text-zinc-200">
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>Hạn mức gấp 5 lần cho mô hình suy luận cao cấp</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>Ưu tiên xử lý trong giờ cao điểm</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>Cửa sổ ngữ cảnh mở rộng 200k token</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>Trải nghiệm sớm các tính năng thử nghiệm</span>
                </li>
              </ul>
            </div>

            <button
              onClick={() => {
                alert("Cảm ơn bạn đã nâng cấp lên F-Cowork Pro!");
                onClose();
              }}
              className="mt-6 w-full py-2.5 bg-[#d97757] hover:bg-[#c26748] text-white font-semibold text-xs rounded-lg transition-colors cursor-pointer shadow-md"
            >
              Đăng ký gói Pro
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
