import React, { useState, useEffect } from 'react';
import { X, Mic, MicOff } from 'lucide-react';

interface VoiceModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSendTranscript?: (text: string) => void;
}

export const VoiceModal: React.FC<VoiceModalProps> = ({ isOpen, onClose, onSendTranscript }) => {
  const [isListening, setIsListening] = useState(true);
  const [transcription, setTranscription] = useState('');

  useEffect(() => {
    if (isOpen && isListening) {
      const phrases = [
        "Analyzing prompt context...",
        "Voice input activated...",
        "How can I help you today?"
      ];
      let i = 0;
      const interval = setInterval(() => {
        setTranscription(phrases[i % phrases.length]);
        i++;
      }, 2000);
      return () => clearInterval(interval);
    }
  }, [isOpen, isListening]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4">
      <div className="bg-[#21201d] border border-[#383632] rounded-3xl w-full max-w-md p-8 shadow-2xl flex flex-col items-center text-center space-y-6 text-zinc-100 animate-in fade-in zoom-in-95 duration-200 relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-2 text-zinc-400 hover:text-zinc-100 hover:bg-[#32302c] rounded-full transition-colors"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Header */}
        <div>
          <h3 className="font-semibold text-lg text-zinc-100">Live Voice Mode</h3>
          <p className="text-xs text-zinc-400 mt-1">Speak naturally to converse with Sonnet 5</p>
        </div>

        {/* Animated Waveform Visualizer */}
        <div className="h-24 flex items-center justify-center gap-2 px-6">
          <div className="w-1.5 bg-[#d97757] rounded-full animate-wave-1" />
          <div className="w-1.5 bg-[#d97757] rounded-full animate-wave-2" />
          <div className="w-1.5 bg-[#d97757] rounded-full animate-wave-3" />
          <div className="w-1.5 bg-[#d97757] rounded-full animate-wave-4" />
          <div className="w-1.5 bg-[#d97757] rounded-full animate-wave-5" />
        </div>

        {/* Dynamic Transcription text */}
        <div className="min-h-[40px] text-sm text-zinc-300 font-medium italic px-4">
          "{transcription || 'Listening...'}"
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-4 pt-2">
          <button
            onClick={() => setIsListening(!isListening)}
            className={`w-14 h-14 rounded-full flex items-center justify-center shadow-lg transition-all cursor-pointer ${
              isListening
                ? 'bg-[#d97757] hover:bg-[#c26748] text-white'
                : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-400'
            }`}
          >
            {isListening ? <Mic className="w-6 h-6" /> : <MicOff className="w-6 h-6" />}
          </button>
        </div>

        <button
          onClick={() => {
            if (onSendTranscript && transcription) {
              onSendTranscript(transcription);
            }
            onClose();
          }}
          className="w-full py-2.5 bg-[#2c2a26] hover:bg-[#383632] text-zinc-200 text-xs font-semibold rounded-xl border border-zinc-700/50 transition-colors cursor-pointer"
        >
          Send Recorded Text
        </button>
      </div>
    </div>
  );
};
