"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

export default function Home() {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [streams, setStreams] = useState<Array<{ id: number; name: string; url: string; backendId?: string; processorId?: string }>>([]);
  const [fullscreenLocalId, setFullscreenLocalId] = useState<number | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [showRename, setShowRename] = useState(false);
  const [renameLocalId, setRenameLocalId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const nextId = useMemo(() => (streams.length ? Math.max(...streams.map(s => s.id)) + 1 : 1), [streams]);
  const [logs] = useState<string[]>([
    "System ready",
    "Awaiting streams",
    "No alerts",
  ]);

  // Stop all active backend streams when leaving the page (browser close/refresh)
  useEffect(() => {
    const handleBeforeUnload = () => {
      const ids = streams.map((s) => s.processorId).filter(Boolean) as string[];
      ids.forEach((pid) => {
        try {
          // keepalive allows the request to be sent during unload
          fetch("/api/stop_stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ stream_id: pid }),
            keepalive: true as any,
          }).catch(() => {});
        } catch {}
      });
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  }, [streams]);

  const StreamLabel = ({ label }: { label: string }) => (
    <span className="text-white/70">{label}</span>
  );

  function RightPanel() {
    const [stats, setStats] = useState<{ total_faces?: number; suspicious_faces?: number; clean_faces?: number; database_entries?: number } | null>(null);
    useEffect(() => {
      let timer: any;
      const poll = async () => {
        try {
          const res = await fetch("/api/shared_stats", { cache: "no-store" });
          const data = await res.json().catch(() => ({}));
          setStats(data || {});
        } catch {}
        timer = setTimeout(poll, 2000);
      };
      poll();
      return () => {
        if (timer) clearTimeout(timer);
      };
    }, []);

    return (
      <aside className="bg-subpanel rounded-2xl p-6 h-full flex flex-col gap-6">
        <div>
          <h3 className="text-xl font-semibold mb-4">Stats</h3>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Total Faces" value={stats?.total_faces ?? 0} />
            <Stat label="Suspicious" value={stats?.suspicious_faces ?? 0} />
            <Stat label="Clean" value={stats?.clean_faces ?? 0} />
            <Stat label="DB Entries" value={stats?.database_entries ?? 0} />
          </div>
        </div>

        <div className="flex-1 min-h-0">
          <h3 className="text-xl font-semibold mb-3">Suspects Found</h3>
          <div className="space-y-3 overflow-auto pr-1">
            {/* Hardcoded entries for now */}
            <SuspectCard name="John Doe" stream="Entrance Cam" time="2025-09-25 14:22:10" img="/vercel.svg" />
            <SuspectCard name="Jane Roe" stream="Lobby Cam" time="2025-09-25 14:18:47" img="/next.svg" />
          </div>
        </div>

        <Link href="/logs" className="mt-2 h-12 rounded-full btn-white px-6 text-lg font-medium self-start grid place-items-center">
          View All
        </Link>
      </aside>
    );
  }

  function Stat({ label, value }: { label: string; value: number }) {
    return (
      <div className="rounded-xl bg-chip text-white/90 px-4 py-3">
        <div className="text-sm text-white/70">{label}</div>
        <div className="text-2xl font-semibold">{value}</div>
      </div>
    );
  }

  function SuspectCard({ name, stream, time, img }: { name: string; stream: string; time: string; img: string }) {
    return (
      <div className="flex items-center gap-3 rounded-xl bg-chip px-3 py-2 text-white/90">
        <img src={img} alt={name} className="h-10 w-10 rounded object-cover bg-white" />
        <div className="min-w-0">
          <div className="font-medium truncate">{name}</div>
          <div className="text-xs text-white/70 truncate">{stream} • {time}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-dvh w-full bg-black text-white overflow-hidden">
      <div className="mx-auto max-w-[1200px] px-6 py-6 h-full">
        {/* Header */}
        <h1 className="text-[32px] font-bold tracking-wide">LIVE FACE DETECTION</h1>

        {/* Content area */}
        <div className="mt-6 h-[calc(100%-64px)] grid grid-cols-[1fr_320px] gap-6">
          {/* Stream tiles container */}
          <div className="bg-panel rounded-2xl p-6 h-full overflow-auto">
            <div className="flex flex-wrap gap-6">
              {streams.map((s) => (
                <div
                  key={s.id}
                  className="relative w-[220px] h-[130px] flex-none"
                  onClick={() => {
                    setIsFullscreen(true);
                    setFullscreenLocalId(s.id);
                  }}
                  role="button"
                  aria-label={`Expand ${s.name}`}
                  title="Expand"
                >
                  {s.processorId ? (
                    <>
                      <img
                        src={`/api/video_feed/${encodeURIComponent(s.processorId)}`}
                        alt={s.name}
                        className={`${isFullscreen && fullscreenLocalId === s.id ? "fixed inset-0 z-40 w-full h-full object-contain bg-black" : "absolute inset-0 w-full h-full object-cover rounded-2xl"}`}
                      />
                      {/* Name overlay on video */}
                      {!isFullscreen && (
                        <div className="absolute inset-x-0 bottom-0 bg-black/40 text-white px-3 py-1 rounded-b-2xl pointer-events-none">
                          <span className="text-xs">{s.name}</span>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="absolute inset-0 bg-[#8f8f8f] text-black rounded-2xl flex items-end justify-start p-4">
                      <span className="text-white/90">{s.name}</span>
                    </div>
                  )}
                  {isFullscreen && fullscreenLocalId === s.id && (
                    <button
                      aria-label="Minimize stream"
                      onClick={(e) => { e.stopPropagation(); setIsFullscreen(false); }}
                      className="fixed right-4 top-4 h-8 w-8 grid place-items-center rounded-md bg-white/70 text-black hover:bg-white transition z-50"
                      title="Minimize"
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5">
                        <path d="M9 9L4 9L4 4M15 15L20 15L20 20" />
                        <path d="M9 9L4 4M15 15L20 20" />
                      </svg>
                    </button>
                  )}
                  {/* Rename */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowRename(true);
                      setRenameLocalId(s.id);
                      setRenameValue(s.name);
                    }}
                    className="absolute -left-2 -top-2 h-7 w-7 rounded-full bg-black/70 text-white grid place-items-center hover:bg-black"
                    aria-label={`Rename ${s.name}`}
                    title="Rename"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
                      <path d="M12 20h9" />
                      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
                    </svg>
                  </button>
                  {/* Delete */}
                  <button
                    onClick={async (e) => {
                      e.stopPropagation();
                      try {
                        const requests: Promise<Response>[] = [];
                        if (s.processorId) {
                          requests.push(
                            fetch("/api/stop_stream", {
                              method: "POST",
                              headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({ stream_id: s.processorId }),
                            })
                          );
                        }
                        if (s.backendId) {
                          requests.push(
                            fetch(`/api/streams?id=${encodeURIComponent(s.backendId)}`, { method: "DELETE" })
                          );
                        }
                        if (requests.length) {
                          await Promise.allSettled(requests);
                        }
                      } finally {
                        setStreams((prev) => prev.filter((x) => x.id !== s.id));
                      }
                    }}
                    className="absolute -right-2 -top-2 h-7 w-7 rounded-full bg-black/70 text-white grid place-items-center hover:bg-black"
                    aria-label={`Delete ${s.name}`}
                    title="Delete"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
                      <path d="M3 6h18M9 6v12m6-12v12M5 6l1 14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-14M8 6l1-2h6l1 2" />
                    </svg>
                  </button>
                </div>
              ))}
              {/* Add tile */}
              <button
                onClick={() => {
                  setShowAdd(true);
                  setNewName("");
                  setNewUrl("");
                }}
                className="bg-tile rounded-2xl w-[220px] h-[130px] flex items-center justify-center hover:opacity-90 transition flex-none"
                aria-label="Add stream"
                title="Add"
              >
                <div className="relative h-8 w-8">
                  <div className="absolute left-1/2 top-0 h-8 w-[2px] -translate-x-1/2 bg-black/70"></div>
                  <div className="absolute top-1/2 left-0 w-8 h-[2px] -translate-y-1/2 bg-black/70"></div>
                </div>
              </button>
            </div>
          </div>

          {/* Right: stats + suspects */}
          <RightPanel />

          {/* Fullscreen overlay */}
          {isFullscreen && (
            <div className="fixed inset-0 bg-black/50 z-50 grid place-items-center p-6">
              <div className="relative w-full max-w-5xl h-[80dvh] rounded-2xl bg-tile-active text-black overflow-hidden">
                <button
                  aria-label="Minimize stream"
                  onClick={() => setIsFullscreen(false)}
                  className="absolute right-4 top-4 h-8 w-8 grid place-items-center rounded-md bg-white/70 text-black hover:bg-white transition z-20"
                  title="Minimize"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5">
                    <path d="M9 9L4 9L4 4M15 15L20 15L20 20" />
                    <path d="M9 9L4 4M15 15L20 20" />
                  </svg>
                </button>
                {(() => {
                  const s = streams.find((x) => x.id === fullscreenLocalId) || streams[0];
                  if (!s) return null;
                  return (
                    <>
                      {s.processorId ? (
                        <img
                          src={`/api/video_feed/${encodeURIComponent(s.processorId)}`}
                          alt={s.name}
                          className="absolute inset-0 w-full h-full object-contain bg-black"
                        />
                      ) : (
                        <div className="absolute inset-0 bg-[#8f8f8f]" />
                      )}
                      <div className="absolute bottom-4 right-4 text-white text-lg">
                        <StreamLabel label={s.name} />
                      </div>
                    </>
                  );
                })()}
              </div>
            </div>
          )}

          {/* Add Stream Modal */}
          {showAdd && (
            <div className="fixed inset-0 bg-black/40 z-50 grid place-items-center p-6">
              <div className="w-full max-w-[520px] rounded-2xl bg-[#3a3a3a] p-8 shadow-lg">
                <h2 className="text-2xl font-semibold">Paste the link</h2>
                <form
                  className="mt-6 space-y-4"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const name = newName.trim() || `stream ${nextId}`;
                    const url = newUrl.trim();
                    let processorId: string | undefined = undefined;
                    try {
                      const startRes = await fetch("/api/start_stream", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ url }),
                      });
                      const startData = await startRes.json().catch(() => ({}));
                      processorId = startData?.stream_id;
                    } catch {}

                    try {
                      const res = await fetch("/api/streams", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ name, url }),
                      });
                      const data = await res.json().catch(() => ({}));
                      const backendId: string | undefined = data?.stream?.id;
                      setStreams((prev) => [...prev, { id: nextId, name, url, backendId, processorId }]);
                    } catch {
                      // fallback: still add locally
                      setStreams((prev) => [...prev, { id: nextId, name, url, processorId }]);
                    }
                    setShowAdd(false);
                  }}
                >
                  <div className="space-y-2">
                    <label className="block text-sm text-white/80">Name</label>
                    <input
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder={`stream ${nextId}`}
                      className="w-full h-11 rounded-full px-4 text-black outline-none border-0 bg-white"
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="block text-sm text-white/80">Link</label>
                    <input
                      value={newUrl}
                      onChange={(e) => setNewUrl(e.target.value)}
                      placeholder="https://..."
                      className="w-full h-11 rounded-full px-4 text-black outline-none border-0 bg-white"
                    />
                  </div>
                  <div className="flex items-center gap-3 pt-2">
                    <button
                      type="submit"
                      className="h-11 px-6 rounded-full btn-white text-base font-medium"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowAdd(false)}
                      className="h-11 px-6 rounded-full bg-chip text-white/90 text-base"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

          {/* Rename Stream Modal */}
          {showRename && (
            <div className="fixed inset-0 bg-black/40 z-50 grid place-items-center p-6">
              <div className="w-full max-w-[420px] rounded-2xl bg-[#3a3a3a] p-8 shadow-lg">
                <h2 className="text-2xl font-semibold">Rename stream</h2>
                <form
                  className="mt-6 space-y-4"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const trimmed = renameValue.trim();
                    if (!trimmed || renameLocalId == null) {
                      setShowRename(false);
                      return;
                    }
                    let backendId: string | undefined;
                    setStreams((prev) => {
                      const next = prev.map((x) => {
                        if (x.id === renameLocalId) {
                          backendId = x.backendId;
                          return { ...x, name: trimmed };
                        }
                        return x;
                      });
                      return next;
                    });
                    if (backendId) {
                      try {
                        await fetch(`/api/streams`, {
                          method: "PATCH",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ id: backendId, name: trimmed }),
                        });
                      } catch {}
                    }
                    setShowRename(false);
                  }}
                >
                  <div className="space-y-2">
                    <label className="block text-sm text-white/80">New name</label>
                    <input
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      placeholder="Enter a name"
                      className="w-full h-11 rounded-full px-4 text-black outline-none border-0 bg-white"
                    />
                  </div>
                  <div className="flex items-center gap-3 pt-2">
                    <button
                      type="submit"
                      className="h-11 px-6 rounded-full btn-white text-base font-medium"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowRename(false)}
                      className="h-11 px-6 rounded-full bg-chip text-white/90 text-base"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
