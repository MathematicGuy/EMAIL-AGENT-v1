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
              <h2 className="font-semibold text-lg text-zinc-100">Upgrade your Plan</h2>
              <p className="text-xs text-zinc-400">Unlock higher limits, faster reasoning models, and advanced tools.</p>
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
              <div className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">Current Plan</div>
              <div className="text-xl font-bold text-zinc-100 mb-1">Free</div>
              <div className="text-2xl font-semibold text-zinc-300 mb-4">$0 <span className="text-xs font-normal text-zinc-500">/ month</span></div>

              <ul className="space-y-2 text-xs text-zinc-300">
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-zinc-500" />
                  <span>Access to standard Sonnet 5</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-zinc-500" />
                  <span>Standard response speeds</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-zinc-500" />
                  <span>Basic context history window</span>
                </li>
              </ul>
            </div>

            <button
              disabled
              className="mt-6 w-full py-2 bg-[#2c2a26] text-zinc-500 font-medium text-xs rounded-lg cursor-default"
            >
              Current Active Plan
            </button>
          </div>

          {/* Pro Plan Card */}
          <div className="p-4 rounded-xl bg-[#2b2925] border-2 border-[#d97757] flex flex-col justify-between relative shadow-lg">
            <span className="absolute -top-3 right-4 bg-[#d97757] text-white text-[10px] font-bold px-2.5 py-0.5 rounded-full uppercase tracking-wider">
              Recommended
            </span>
            <div>
              <div className="text-xs font-semibold text-[#d97757] uppercase tracking-wider mb-1">Pro Plan</div>
              <div className="text-xl font-bold text-zinc-100 mb-1">F-Cowork Pro</div>
              <div className="text-2xl font-semibold text-zinc-100 mb-4">$20 <span className="text-xs font-normal text-zinc-400">/ month</span></div>

              <ul className="space-y-2 text-xs text-zinc-200">
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>5x more usage on Fable 5 & Sonnet 5</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>Priority access during peak traffic</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>Extended 200k token context window</span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="w-3.5 h-3.5 text-[#d97757]" />
                  <span>Early access to new experimental features</span>
                </li>
              </ul>
            </div>

            <button
              onClick={() => {
                alert("Thank you for upgrading to F-Cowork Pro!");
                onClose();
              }}
              className="mt-6 w-full py-2.5 bg-[#d97757] hover:bg-[#c26748] text-white font-semibold text-xs rounded-lg transition-colors cursor-pointer shadow-md"
            >
              Subscribe to Pro
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
