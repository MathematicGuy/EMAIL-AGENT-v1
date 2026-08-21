import React, { useState, useEffect, useRef } from 'react';
import {
  Sparkles,
  ArrowRight,
  Zap,
  Bot,
  Layers,
  ShieldCheck,
  CheckCircle2,
  Cpu
} from 'lucide-react';

interface LandingPageProps {
  onNavigateToDashboard: () => void;
}

export const LandingPage: React.FC<LandingPageProps> = ({ onNavigateToDashboard }) => {
  const [typedText, setTypedText] = useState('');
  const [mousePos, setMousePos] = useState({ x: -1000, y: -1000 });
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const mousePosRef = useRef({ x: -1000, y: -1000 });
  const fullPrompt = "Xây dựng dashboard phân tích theo thời gian thực với React & Tailwind...";

  // Mouse position tracker for dynamic interactive spotlight & particle connections (Effect 1)
  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const newPos = {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top
    };
    setMousePos(newPos);
    mousePosRef.current = newPos;
  };

  // Continuous infinite typewriter loop effect
  useEffect(() => {
    let index = 0;
    let timeoutId: ReturnType<typeof setTimeout>;

    const typeNextChar = () => {
      if (index <= fullPrompt.length) {
        setTypedText(fullPrompt.slice(0, index));
        index++;
        timeoutId = setTimeout(typeNextChar, 45);
      } else {
        timeoutId = setTimeout(() => {
          index = 0;
          setTypedText('');
          typeNextChar();
        }, 2500);
      }
    };

    typeNextChar();
    return () => clearTimeout(timeoutId);
  }, []);

  // Effect 1: Bright Constellation network + Effect 2: 6s Periodic Meteor Shower Wave
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;
    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    const handleResize = () => {
      if (!canvas) return;
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    // --- EFFECT 1: Bright Constellation Particles (75 count) ---
    const particleCount = 75;
    const particles: Array<{
      x: number;
      y: number;
      vx: number;
      vy: number;
      radius: number;
      opacity: number;
    }> = [];

    for (let i = 0; i < particleCount; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.7,
        vy: (Math.random() - 0.5) * 0.7,
        radius: Math.random() * 1.8 + 1.0,
        opacity: Math.random() * 0.45 + 0.45
      });
    }

    // --- EFFECT 2: 6s Periodic Pure White Shooting Star Meteor Shower ---
    const meteors: Array<{
      x: number;
      y: number;
      length: number;
      speed: number;
      opacity: number;
      angle: number;
      width: number;
    }> = [];

    const spawnMeteorShowerWave = () => {
      // Spawn a wave of 2-3 meteors each with unique random angle and length
      const count = Math.floor(Math.random() * 2) + 2;
      for (let mIdx = 0; mIdx < count; mIdx++) {
        const angle = Math.random() * 0.85 + 0.35; // Unique trajectory angle
        const startX = Math.random() * (width * 1.2) - width * 0.1;
        const startY = Math.random() * (height * 0.35) - 80;

        meteors.push({
          x: startX,
          y: startY,
          length: Math.random() * 200 + 90,
          speed: Math.random() * 4.5 + 3.5,
          opacity: 1.0,
          angle: angle,
          width: Math.random() * 2.2 + 1.5
        });
      }
    };

    // Meteor shower wave spawns exactly every 6 seconds
    const meteorInterval = setInterval(() => {
      spawnMeteorShowerWave();
    }, 6000);

    // Initial meteor wave after 1s
    setTimeout(() => {
      spawnMeteorShowerWave();
    }, 1000);

    const render = () => {
      ctx.clearRect(0, 0, width, height);

      const m = mousePosRef.current;

      // -------------------------------------------------------------
      // RENDER EFFECT 2: 6s Periodic Meteor Shower Wave
      // -------------------------------------------------------------
      for (let i = meteors.length - 1; i >= 0; i--) {
        const star = meteors[i];

        const tailX = star.x - Math.cos(star.angle) * star.length;
        const tailY = star.y - Math.sin(star.angle) * star.length;

        const grad = ctx.createLinearGradient(star.x, star.y, tailX, tailY);
        grad.addColorStop(0, `rgba(255, 255, 255, ${star.opacity})`);
        grad.addColorStop(0.2, `rgba(255, 255, 255, ${star.opacity * 0.9})`);
        grad.addColorStop(0.65, `rgba(240, 245, 255, ${star.opacity * 0.35})`);
        grad.addColorStop(1, `rgba(255, 255, 255, 0)`);

        ctx.beginPath();
        ctx.moveTo(star.x, star.y);
        ctx.lineTo(tailX, tailY);
        ctx.lineWidth = star.width;
        ctx.strokeStyle = grad;
        ctx.lineCap = 'round';

        ctx.shadowBlur = 20;
        ctx.shadowColor = '#ffffff';
        ctx.stroke();
        ctx.shadowBlur = 0;

        ctx.beginPath();
        ctx.arc(star.x, star.y, star.width * 1.5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${star.opacity})`;
        ctx.shadowBlur = 15;
        ctx.shadowColor = '#ffffff';
        ctx.fill();
        ctx.shadowBlur = 0;

        star.x += Math.cos(star.angle) * star.speed;
        star.y += Math.sin(star.angle) * star.speed;
        star.opacity -= 0.0035;

        if (star.opacity <= 0 || star.y > height + 250 || star.x > width + 250) {
          meteors.splice(i, 1);
        }
      }

      // -------------------------------------------------------------
      // RENDER EFFECT 1: Bright Vibrant Constellation Particles (75 count)
      // -------------------------------------------------------------
      for (let i = 0; i < particleCount; i++) {
        const p1 = particles[i];

        p1.x += p1.vx;
        p1.y += p1.vy;

        if (p1.x < 0 || p1.x > width) p1.vx *= -1;
        if (p1.y < 0 || p1.y > height) p1.vy *= -1;

        ctx.beginPath();
        ctx.arc(p1.x, p1.y, p1.radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 184, 153, ${p1.opacity})`;
        ctx.shadowBlur = 10;
        ctx.shadowColor = '#ff9d76';
        ctx.fill();
        ctx.shadowBlur = 0;

        for (let j = i + 1; j < particleCount; j++) {
          const p2 = particles[j];
          const dx = p1.x - p2.x;
          const dy = p1.y - p2.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < 130) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            const lineAlpha = (1 - dist / 130) * 0.32;
            ctx.strokeStyle = `rgba(255, 157, 118, ${lineAlpha})`;
            ctx.lineWidth = 0.9;
            ctx.stroke();
          }
        }

        if (m.x > 0 && m.y > 0) {
          const mDx = p1.x - m.x;
          const mDy = p1.y - m.y;
          const mDist = Math.sqrt(mDx * mDx + mDy * mDy);

          if (mDist < 180) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(m.x, m.y);
            const mAlpha = (1 - mDist / 180) * 0.55;
            ctx.strokeStyle = `rgba(255, 184, 153, ${mAlpha})`;
            ctx.lineWidth = 1.2;
            ctx.stroke();
          }
        }
      }

      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => {
      window.removeEventListener('resize', handleResize);
      clearInterval(meteorInterval);
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return (
    <div
      onMouseMove={handleMouseMove}
      className="min-h-screen bg-[#121110] text-[#f3f2ef] font-sans relative overflow-x-hidden selection:bg-[#d97757]/30 selection:text-white"
    >
      {/* Dynamic Cybernetic Perspective Grid Lines Background */}
      <div
        className="pointer-events-none absolute inset-0 z-0 opacity-20"
        style={{
          backgroundImage: `
            linear-gradient(to right, rgba(255, 255, 255, 0.04) 1px, transparent 1px),
            linear-gradient(to bottom, rgba(255, 255, 255, 0.04) 1px, transparent 1px)
          `,
          backgroundSize: '48px 48px',
          maskImage: 'radial-gradient(ellipse 80% 50% at 50% 30%, black 40%, transparent 100%)',
          WebkitMaskImage: 'radial-gradient(ellipse 80% 50% at 50% 30%, black 40%, transparent 100%)'
        }}
      />

      {/* Interactive Mouse Spotlight Follower */}
      <div
        className="pointer-events-none absolute inset-0 z-0 transition-opacity duration-300"
        style={{
          background: `radial-gradient(700px circle at ${mousePos.x}px ${mousePos.y}px, rgba(217, 119, 87, 0.14), transparent 50%)`
        }}
      />

      {/* Effect 1 (Constellation) + Effect 2 (6s Periodic Meteor Shower) Canvas */}
      <canvas
        ref={canvasRef}
        className="pointer-events-none fixed inset-0 z-10 opacity-100"
      />

      {/* Ambient Rotating Plasma Glow Orbs */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[1100px] h-[550px] bg-gradient-to-b from-[#d97757]/20 via-[#e89b82]/8 to-transparent blur-3xl rounded-full pointer-events-none animate-pulse duration-10000 z-0" />
      <div className="absolute top-1/3 -left-48 w-96 h-96 bg-[#d97757]/14 blur-3xl rounded-full pointer-events-none z-0 animate-spin duration-20000" />
      <div className="absolute bottom-1/4 -right-48 w-96 h-96 bg-amber-500/14 blur-3xl rounded-full pointer-events-none z-0 animate-pulse duration-8000" />

      {/* Header Bar */}
      <header className="relative z-20 max-w-6xl mx-auto px-6 py-6 flex items-center justify-between">
        {/* Brand Logo using f-cowork-logo-no-tagline.svg */}
        <div className="flex items-center">
          <img
            src="/images/f-cowork-logo-no-tagline.svg"
            alt="F-Cowork Logo"
            className="h-10 sm:h-12 object-contain max-w-[210px] drop-shadow-md hover:scale-105 transition-transform"
          />
        </div>

        {/* Clean Single Header CTA */}
        <button
          onClick={onNavigateToDashboard}
          className="group relative inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#d97757] via-[#e08365] to-[#c76545] text-white text-xs font-semibold shadow-lg shadow-[#d97757]/25 hover:shadow-[#d97757]/50 hover:scale-105 transition-all duration-300 cursor-pointer overflow-hidden"
        >
          <span className="relative z-10 flex items-center gap-1.5">
            <span>Mở Không Gian Làm Việc</span>
            <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform" />
          </span>
          <div className="absolute inset-0 bg-white/20 opacity-0 group-hover:opacity-100 transition-opacity" />
        </button>
      </header>

      {/* Main Ultra-Minimal Hero Section */}
      <main className="relative z-20 max-w-5xl mx-auto px-6 pt-10 pb-20 text-center flex flex-col items-center">
        {/* Simplified Tagline Pill */}
        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-[#23211e]/90 border border-[#383531] text-xs font-medium text-[#e89b82] mb-6 shadow-md backdrop-blur-md hover:border-[#d97757]/40 transition-colors">
          <Sparkles className="w-3.5 h-3.5 text-[#d97757] animate-spin duration-5000" />
          <span>F-Cowork · Không Gian Làm Việc AI</span>
        </div>

        {/* Original Headline */}
        <h1 className="text-4xl sm:text-6xl md:text-7xl font-serif tracking-tight text-[#f3f2ef] leading-[1.1] mb-6 font-normal max-w-4xl drop-shadow-sm">
          Biến Yêu Cầu Thành{' '}
          <span className="bg-gradient-to-r from-[#f3f2ef] via-[#e89b82] to-[#d97757] bg-clip-text text-transparent italic font-light">
            Quy Trình AI
          </span>
        </h1>

        {/* Hero CTA Button */}
        <div className="mb-14">
          <button
            onClick={onNavigateToDashboard}
            className="group relative inline-flex items-center gap-3 px-8 py-4 rounded-2xl bg-gradient-to-r from-[#d97757] via-[#e08365] to-[#c76545] text-white font-semibold text-sm shadow-xl shadow-[#d97757]/30 hover:shadow-[#d97757]/60 hover:scale-105 transition-all duration-300 cursor-pointer overflow-hidden"
          >
            <span className="relative z-10 flex items-center gap-2.5">
              <Zap className="w-4 h-4 fill-current" />
              <span>Dùng Thử F-Cowork Miễn Phí</span>
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1.5 transition-transform" />
            </span>
            <div className="absolute inset-0 bg-gradient-to-r from-white/0 via-white/20 to-white/0 -translate-x-full group-hover:translate-x-full transition-transform duration-1000" />
          </button>
        </div>

        {/* Clean Studio Preview Window with Continuous Typewriter Loop */}
        <div className="w-full max-w-4xl rounded-2xl bg-[#1a1917]/90 border border-[#33302b] shadow-2xl hover:border-[#4d4841] transition-all duration-300 overflow-hidden text-left relative group backdrop-blur-sm">
          {/* Mac Window Header Bar */}
          <div className="flex items-center justify-between px-4 py-3 bg-[#23211e]/90 border-b border-[#33302b]">
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-rose-500/80 shadow-xs" />
              <span className="w-3 h-3 rounded-full bg-amber-500/80 shadow-xs" />
              <span className="w-3 h-3 rounded-full bg-emerald-500/80 shadow-xs" />
              <span className="ml-3 text-xs text-zinc-400 font-mono">F-Cowork Studio</span>
            </div>
            <span className="text-[11px] font-mono text-emerald-400 bg-emerald-950/50 px-2 py-0.5 rounded border border-emerald-800/40">
              ● Trực tiếp
            </span>
          </div>

          {/* Typewriter Text Display Area */}
          <div className="p-6 sm:p-8 bg-[#151412]/95 min-h-[260px] flex flex-col justify-between relative overflow-hidden">
            <div className="absolute top-0 right-0 w-80 h-80 bg-[#d97757]/12 blur-3xl rounded-full" />

            <div className="space-y-6 relative z-10">
              <div className="flex items-center justify-between text-xs font-semibold text-[#d97757]">
                <span className="flex items-center gap-2">
                  <Bot className="w-4 h-4" />
                  <span>Thu nạp dữ liệu không gian AI</span>
                </span>
              </div>

              {/* Infinite Continuous Typewriter Box */}
              <div className="p-5 rounded-xl bg-[#22201c] border border-[#383531] text-sm sm:text-base text-zinc-200 font-mono shadow-inner min-h-[72px] leading-relaxed group-hover:border-[#d97757]/30 transition-colors">
                {typedText}
                <span className="inline-block w-2.5 h-5 ml-1 bg-[#d97757] animate-pulse align-middle shadow-[0_0_10px_rgba(217,119,87,0.9)]" />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2">
                <div className="p-3 rounded-xl bg-[#22201c]/90 border border-[#35332f] flex items-center justify-between text-xs hover:border-emerald-500/40 transition-colors">
                  <span className="flex items-center gap-1.5 font-semibold text-emerald-400">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>Tiếp nhận</span>
                  </span>
                  <span className="text-zinc-400 font-mono">100%</span>
                </div>

                <div className="p-3 rounded-xl bg-[#22201c]/90 border border-[#35332f] flex items-center justify-between text-xs hover:border-amber-500/40 transition-colors">
                  <span className="flex items-center gap-1.5 font-semibold text-amber-300">
                    <Cpu className="w-3.5 h-3.5 animate-spin duration-3000" />
                    <span>Kế hoạch</span>
                  </span>
                  <span className="text-zinc-400 font-mono">85%</span>
                </div>

                <div className="p-3 rounded-xl bg-[#22201c]/90 border border-[#35332f] flex items-center justify-between text-xs hover:border-[#d97757]/40 transition-colors">
                  <span className="flex items-center gap-1.5 font-semibold text-[#d97757]">
                    <ShieldCheck className="w-3.5 h-3.5" />
                    <span>Phê duyệt</span>
                  </span>
                  <span className="text-zinc-400 font-mono">Sẵn sàng</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 3 Ultra-Minimal Feature Cards */}
        <section id="features" className="w-full grid grid-cols-1 sm:grid-cols-3 gap-4 mt-16 text-left">
          <div className="p-5 rounded-2xl bg-[#191816]/90 border border-[#2c2a26] hover:border-[#d97757]/50 hover:-translate-y-1 transition-all duration-300 shadow-md group backdrop-blur-sm">
            <Bot className="w-5 h-5 text-[#d97757] mb-3 group-hover:scale-110 transition-transform" />
            <h3 className="text-sm font-semibold text-zinc-100 mb-1">Trò chuyện Đa Mô hình</h3>
            <p className="text-xs text-zinc-400">Gemini, Claude, DeepSeek với khả năng suy luận chuyên sâu.</p>
          </div>

          <div className="p-5 rounded-2xl bg-[#191816]/90 border border-[#2c2a26] hover:border-amber-400/50 hover:-translate-y-1 transition-all duration-300 shadow-md group backdrop-blur-sm">
            <Layers className="w-5 h-5 text-amber-400 mb-3 group-hover:scale-110 transition-transform" />
            <h3 className="text-sm font-semibold text-zinc-100 mb-1">Xem trước Artifact Trực tiếp</h3>
            <p className="text-xs text-zinc-400">Xem trước mã nguồn và giao diện thời gian thực song song.</p>
          </div>

          <div className="p-5 rounded-2xl bg-[#191816]/90 border border-[#2c2a26] hover:border-emerald-400/50 hover:-translate-y-1 transition-all duration-300 shadow-md group backdrop-blur-sm">
            <ShieldCheck className="w-5 h-5 text-emerald-400 mb-3 group-hover:scale-110 transition-transform" />
            <h3 className="text-sm font-semibold text-zinc-100 mb-1">Kiểm duyệt & Phê duyệt</h3>
            <p className="text-xs text-zinc-400">Thực thi có kiểm soát với các cổng phê duyệt rõ ràng.</p>
          </div>
        </section>
      </main>
    </div>
  );
};
