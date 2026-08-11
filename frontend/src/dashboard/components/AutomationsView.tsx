import React, { useState, useEffect, useCallback } from 'react';
import {
  Clock,
  Play,
  Plus,
  RefreshCw,
  Pause,
  RotateCcw,
  Trash2,
  Send,
  Calendar,
  X,
  Search,
  CheckCircle,
  Sparkles,
} from 'lucide-react';
import { API_BASE_URL } from '../../lib/apiConfig';

const CRON_PRESETS = [
  { label: '9:00 sáng Thứ Hai hàng tuần', cron: '0 9 * * MON' },
  { label: 'Hàng ngày lúc 12:00 đêm', cron: '0 0 * * *' },
  { label: 'Mỗi 15 phút một lần', cron: '*/15 * * * *' },
  { label: 'Mỗi 1 giờ một lần', cron: '0 * * * *' },
  { label: '12:00 trưa ngày 1 hàng tháng', cron: '0 12 1 * *' },
];

function describeCron(cron: string): string {
  const c = cron.trim();
  if (c === '0 9 * * MON') return '9:00 sáng Thứ Hai hàng tuần';
  if (c === '0 0 * * *') return 'Hàng ngày lúc 12:00 đêm';
  if (c === '*/15 * * * *') return 'Mỗi 15 phút một lần';
  if (c === '0 * * * *') return 'Mỗi 1 giờ một lần';
  if (c === '0 12 1 * *') return '12:00 trưa ngày 1 hàng tháng';
  if (c.startsWith('*/')) return `Mỗi ${c.split(' ')[0].replace('*/', '')} phút một lần`;
  return c;
}

interface Schedule {
  id: string;
  name: string;
  type: string;
  cron: string;
  status: string;
  lastFiredAt?: string;
}

interface AuditLog {
  id: string;
  operation: string;
  result: string;
  createdAt: string;
}

export const AutomationsView: React.FC = () => {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'schedules' | 'history'>('schedules');

  // Search & Filter
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'paused'>('all');

  // Creation modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newScheduleName, setNewScheduleName] = useState('');
  const [newScheduleCron, setNewScheduleCron] = useState('0 9 * * MON');

  // Actions tracking
  const [firingId, setFiringId] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setIsLoading(true);
    try {
      const [schedRes, auditRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/v1/automations/schedules`, {
          cache: 'no-store',
        }),
        fetch(`${API_BASE_URL}/api/v1/automations/audit-logs`, {
          cache: 'no-store',
        }),
      ]);
      if (schedRes.ok) {
        const data = await schedRes.json();
        setSchedules(data.schedules ?? []);
      }
      if (auditRes.ok) {
        const data = await auditRes.json();
        setAuditLogs(data.events ?? []);
      }
    } catch {
      // quiet fallback
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void fetchAll(), 0);
    const interval = setInterval(fetchAll, 3000);
    return () => {
      clearTimeout(initial);
      clearInterval(interval);
    };
  }, [fetchAll]);

  const handleCreateSchedule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newScheduleName.trim()) return;

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/automations/schedules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newScheduleName,
          cron: newScheduleCron,
          type: 'recurring',
          timezone: 'Asia/Ho_Chi_Minh',
        }),
      });
      if (res.ok) {
        setNewScheduleName('');
        setIsCreateOpen(false);
        setSuccessMessage('Đã thêm lịch tự động mới!');
        setTimeout(() => setSuccessMessage(null), 3000);
        fetchAll();
      }
    } catch {
      // fallback
    }
  };

  const handleFireSchedule = async (sched: Schedule) => {
    setFiringId(sched.id);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/automations/schedules/${sched.id}/fire`, {
        method: 'POST',
      });
      if (res.ok) {
        setSuccessMessage(`Đã kích hoạt chạy ngay công việc "${sched.name}"!`);
        setTimeout(() => setSuccessMessage(null), 4000);
      }
      fetchAll();
    } catch {
      // fallback
    } finally {
      setFiringId(null);
    }
  };

  const handleManageSchedule = async (id: string, command: 'pause' | 'resume') => {
    try {
      await fetch(`${API_BASE_URL}/api/v1/automations/schedules/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command }),
      });
      fetchAll();
    } catch {
      // fallback
    }
  };

  const handleDeleteSchedule = async (id: string) => {
    if (!confirm('Xóa lịch tự động này?')) return;
    try {
      await fetch(`${API_BASE_URL}/api/v1/automations/schedules/${id}`, { method: 'DELETE' });
      fetchAll();
    } catch {
      // fallback
    }
  };

  const activeCount = schedules.filter((s) => s.status === 'active').length;
  const pausedCount = schedules.filter((s) => s.status === 'paused').length;

  const filteredSchedules = schedules.filter((s) => {
    const matchesSearch = s.name.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'all' || s.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  return (
    <div className="flex-1 flex flex-col h-full bg-[#1c1b18] text-[#f3f2ef] overflow-y-auto font-sans select-none">
      {/* ------------------------------------------------------------------- */}
      {/* MINIMALIST HEADER                                                   */}
      {/* ------------------------------------------------------------------- */}
      <header className="sticky top-0 z-20 bg-[#1c1b18]/90 backdrop-blur-xl border-b border-[#33312e]/60 px-8 py-6">
        <div className="max-w-5xl mx-auto flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-[#d97757]/15 border border-[#d97757]/30 flex items-center justify-center text-[#d97757]">
              <Clock className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white tracking-tight font-serif">Lịch Tự Động</h1>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={fetchAll}
              disabled={isLoading}
              className="p-2.5 bg-[#292825] hover:bg-[#34322f] text-[#949089] hover:text-white rounded-xl border border-[#33312e] transition-all cursor-pointer"
            >
              <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            </button>

            <button
              onClick={() => setIsCreateOpen(true)}
              className="flex items-center gap-2 px-4 py-2.5 bg-[#d97757] hover:bg-[#e08566] text-white text-xs font-semibold rounded-xl shadow-lg shadow-[#d97757]/20 transition-all cursor-pointer active:scale-95"
            >
              <Plus className="w-4 h-4" />
              <span>Tạo Lịch Mới</span>
            </button>
          </div>
        </div>
      </header>

      {/* ------------------------------------------------------------------- */}
      {/* MAIN CONTAINER                                                      */}
      {/* ------------------------------------------------------------------- */}
      <main className="max-w-5xl mx-auto px-8 py-6 w-full space-y-6">
        {/* Floating Corner Toast Notification */}
        {successMessage && (
          <div className="fixed bottom-6 right-6 z-50 bg-[#292825] border border-emerald-500/40 rounded-2xl px-4 py-3 text-xs text-white shadow-2xl flex items-center gap-3 animate-in slide-in-from-bottom-5 fade-in duration-300">
            <div className="w-8 h-8 rounded-xl bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center justify-center shrink-0">
              <CheckCircle className="w-4 h-4" />
            </div>
            <div className="space-y-0.5">
              <span className="font-bold text-emerald-300 block">Thông báo</span>
              <span className="text-[#f3f2ef] font-medium">{successMessage}</span>
            </div>
            <button
              onClick={() => setSuccessMessage(null)}
              className="ml-2 p-1 text-[#949089] hover:text-white rounded-lg cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* Minimal Stats Row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-[#292825]/60 border border-[#33312e] rounded-2xl p-4 flex items-center justify-between">
            <div>
              <span className="text-xs text-[#949089] block mb-0.5">Đang chạy</span>
              <span className="text-xl font-bold text-white">{activeCount}</span>
            </div>
            <div className="w-8 h-8 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
              <Play className="w-4 h-4" />
            </div>
          </div>

          <div className="bg-[#292825]/60 border border-[#33312e] rounded-2xl p-4 flex items-center justify-between">
            <div>
              <span className="text-xs text-[#949089] block mb-0.5">Đã tạm dừng</span>
              <span className="text-xl font-bold text-amber-400">{pausedCount}</span>
            </div>
            <div className="w-8 h-8 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400">
              <Pause className="w-4 h-4" />
            </div>
          </div>

          <div className="bg-[#292825]/60 border border-[#33312e] rounded-2xl p-4 flex items-center justify-between">
            <div>
              <span className="text-xs text-[#949089] block mb-0.5">Tổng lượt đã chạy</span>
              <span className="text-xl font-bold text-blue-400">{auditLogs.length}</span>
            </div>
            <div className="w-8 h-8 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
              <Send className="w-4 h-4" />
            </div>
          </div>
        </div>

        {/* Tab Switcher */}
        <div className="flex items-center justify-between border-b border-[#33312e] pb-3">
          <div className="flex gap-2">
            <button
              onClick={() => setActiveTab('schedules')}
              className={`px-4 py-2 text-xs font-semibold rounded-xl transition-all cursor-pointer ${
                activeTab === 'schedules' ? 'bg-[#d97757] text-white shadow-md' : 'text-[#949089] hover:text-white bg-[#242320]'
              }`}
            >
              Danh sách lịch ({schedules.length})
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`px-4 py-2 text-xs font-semibold rounded-xl transition-all cursor-pointer ${
                activeTab === 'history' ? 'bg-[#d97757] text-white shadow-md' : 'text-[#949089] hover:text-white bg-[#242320]'
              }`}
            >
              Lịch sử đã chạy ({auditLogs.length})
            </button>
          </div>

          {activeTab === 'schedules' && (
            <div className="flex items-center gap-2">
              <div className="flex items-center bg-[#242320] border border-[#33312e] rounded-xl px-3 py-1.5 text-xs text-white">
                <Search className="w-3.5 h-3.5 text-[#949089] mr-2" />
                <input
                  type="text"
                  placeholder="Tìm lịch..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="bg-transparent border-none text-xs text-white placeholder-[#6c6862] focus:outline-none w-32"
                />
              </div>

              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as 'all' | 'active' | 'paused')}
                className="bg-[#242320] border border-[#33312e] rounded-xl px-3 py-1.5 text-xs text-[#949089] focus:text-white focus:outline-none cursor-pointer"
              >
                <option value="all">Tất cả</option>
                <option value="active">Đang chạy</option>
                <option value="paused">Tạm dừng</option>
              </select>
            </div>
          )}
        </div>

        {/* ------------------------------------------------------------------- */}
        {/* TAB 1: SCHEDULE CARDS                                              */}
        {/* ------------------------------------------------------------------- */}
        {activeTab === 'schedules' && (
          <div className="space-y-3">
            {filteredSchedules.length === 0 ? (
              <div className="p-12 text-center bg-[#242320]/40 rounded-3xl border border-dashed border-[#33312e] text-[#949089] space-y-3">
                <Clock className="w-8 h-8 text-[#6c6862] mx-auto" />
                <p className="font-semibold text-white text-sm">Chưa có lịch tự động nào</p>
                <button
                  onClick={() => setIsCreateOpen(true)}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-[#d97757] hover:bg-[#e08566] text-white text-xs font-semibold rounded-xl shadow-md transition-all cursor-pointer"
                >
                  <Plus className="w-4 h-4" />
                  <span>Tạo Lịch Mới</span>
                </button>
              </div>
            ) : (
              filteredSchedules.map((sched) => {
                const isFiring = firingId === sched.id;
                const isActive = sched.status === 'active';

                return (
                  <div
                    key={sched.id}
                    className="bg-[#292825] hover:bg-[#2e2d2a] border border-[#33312e] hover:border-[#423f38] rounded-2xl p-4 flex items-center justify-between flex-wrap gap-4 transition-all duration-200"
                  >
                    {/* Left: Info */}
                    <div className="flex items-center gap-4 flex-1 min-w-[280px]">
                      <div
                        className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border ${
                          isActive
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                            : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                        }`}
                      >
                        <Calendar className="w-5 h-5" />
                      </div>

                      <div className="space-y-1">
                        <div className="flex items-center gap-2.5">
                          <h3 className="text-sm font-bold text-white">{sched.name}</h3>
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                              isActive
                                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                                : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                            }`}
                          >
                            {isActive ? 'Đang chạy' : 'Tạm dừng'}
                          </span>
                        </div>

                        <p className="text-xs text-[#949089] flex items-center gap-1.5">
                          <Clock className="w-3.5 h-3.5 text-[#d97757]" />
                          <span>{describeCron(sched.cron)}</span>
                        </p>
                      </div>
                    </div>

                    {/* Right: Actions */}
                    <div className="flex items-center gap-2">
                      {isActive && (
                        <button
                          onClick={() => handleFireSchedule(sched)}
                          disabled={isFiring}
                          className="flex items-center gap-1.5 px-3.5 py-2 bg-[#d97757] hover:bg-[#e08566] text-white text-xs font-semibold rounded-xl shadow-sm transition-all cursor-pointer disabled:opacity-50"
                        >
                          <Play className={`w-3.5 h-3.5 ${isFiring ? 'animate-spin' : ''}`} />
                          <span>{isFiring ? 'Đang chạy…' : 'Chạy ngay'}</span>
                        </button>
                      )}

                      {isActive ? (
                        <button
                          onClick={() => handleManageSchedule(sched.id, 'pause')}
                          title="Tạm dừng"
                          className="p-2 bg-[#1c1b18] hover:bg-[#34322f] rounded-xl text-[#949089] hover:text-amber-400 border border-[#33312e] transition-colors cursor-pointer"
                        >
                          <Pause className="w-4 h-4" />
                        </button>
                      ) : (
                        <button
                          onClick={() => handleManageSchedule(sched.id, 'resume')}
                          title="Tiếp tục chạy"
                          className="p-2 bg-[#1c1b18] hover:bg-[#34322f] rounded-xl text-[#949089] hover:text-emerald-400 border border-[#33312e] transition-colors cursor-pointer"
                        >
                          <RotateCcw className="w-4 h-4" />
                        </button>
                      )}

                      <button
                        onClick={() => handleDeleteSchedule(sched.id)}
                        title="Xóa lịch"
                        className="p-2 bg-[#1c1b18] hover:bg-rose-950/40 rounded-xl text-[#949089] hover:text-rose-400 border border-[#33312e] hover:border-rose-500/30 transition-colors cursor-pointer"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* ------------------------------------------------------------------- */}
        {/* TAB 2: EXECUTION HISTORY                                            */}
        {/* ------------------------------------------------------------------- */}
        {activeTab === 'history' && (
          <div className="space-y-2.5">
            {auditLogs.length === 0 ? (
              <div className="p-12 text-center bg-[#242320]/40 rounded-3xl border border-dashed border-[#33312e] text-[#949089] text-xs">
                Chưa có lịch sử chạy nào.
              </div>
            ) : (
              auditLogs.map((log) => (
                <div key={log.id} className="bg-[#292825] rounded-2xl border border-[#33312e] p-4 flex items-center justify-between text-xs">
                  <div className="flex items-center gap-3">
                    <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
                    <div>
                      <span className="font-bold text-white block">Tác vụ tự động đã chạy thành công</span>
                      <span className="text-[11px] text-[#949089]">Mã sự kiện: {log.id.slice(0, 8)}...</span>
                    </div>
                  </div>
                  <span className="text-[#949089] font-mono text-[11px]">{new Date(log.createdAt).toLocaleTimeString()}</span>
                </div>
              ))
            )}
          </div>
        )}
      </main>

      {/* ------------------------------------------------------------------- */}
      {/* MINIMAL CREATION MODAL                                              */}
      {/* ------------------------------------------------------------------- */}
      {isCreateOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in">
          <div className="bg-[#292825] border border-[#383532] rounded-3xl max-w-md w-full p-6 space-y-5 shadow-2xl">
            <div className="flex items-center justify-between border-b border-[#33312e] pb-4">
              <div className="flex items-center gap-2.5">
                <Sparkles className="w-5 h-5 text-[#d97757]" />
                <h3 className="text-base font-bold text-white">Tạo Lịch Tự Động Mới</h3>
              </div>
              <button onClick={() => setIsCreateOpen(false)} className="text-[#949089] hover:text-white p-1 cursor-pointer">
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateSchedule} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-[#949089] mb-1.5">Tên công việc tự động</label>
                <input
                  type="text"
                  required
                  placeholder="Ví dụ: Tự động gửi báo cáo doanh thu tuần..."
                  value={newScheduleName}
                  onChange={(e) => setNewScheduleName(e.target.value)}
                  className="w-full bg-[#1c1b18] border border-[#3a3732] rounded-xl px-4 py-2.5 text-xs text-white placeholder-[#6c6862] focus:outline-none focus:border-[#d97757]"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-[#949089] mb-1.5">Chọn thời gian chạy</label>
                <div className="space-y-2">
                  {CRON_PRESETS.map((p) => (
                    <button
                      key={p.cron}
                      type="button"
                      onClick={() => setNewScheduleCron(p.cron)}
                      className={`w-full text-left px-3.5 py-2.5 text-xs rounded-xl border transition-all cursor-pointer ${
                        newScheduleCron === p.cron
                          ? 'bg-[#d97757]/15 text-white border-[#d97757]/50 font-semibold'
                          : 'bg-[#1c1b18] text-[#949089] border-[#33312e] hover:text-white'
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-end gap-3 pt-2 border-t border-[#33312e]">
                <button
                  type="button"
                  onClick={() => setIsCreateOpen(false)}
                  className="px-4 py-2 text-xs text-[#949089] hover:text-white cursor-pointer"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  className="px-5 py-2.5 bg-[#d97757] hover:bg-[#e08566] text-white text-xs font-semibold rounded-xl transition-all cursor-pointer shadow-md"
                >
                  Tạo Lịch
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
