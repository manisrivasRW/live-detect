"use client";

import { useMemo, useState } from "react";

export default function Home() {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [streams, setStreams] = useState<Array<{ id: number; name: string; url: string; backendId?: string; processorId?: string }>>([]);
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

  const StreamLabel = ({ label }: { label: string }) => (
    <span className="text-white/70">{label}</span>
  );

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
                <div key={s.id} className="relative w-[220px] h-[130px] flex-none">
                  <button
                    onClick={() => setIsFullscreen(true)}
                    className="absolute inset-0 bg-[#8f8f8f] text-black rounded-2xl flex items-end justify-start p-4 hover:opacity-95 transition cursor-pointer"
                    aria-label={`Expand ${s.name}`}
                    title="Expand"
                  >
                    <span className="text-white/90">{s.name}</span>
                  </button>
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
                      setStreams((prev) => prev.filter((x) => x.id !== s.id));
                      if (s.backendId) {
                        try {
                          await fetch(`/api/streams?id=${encodeURIComponent(s.backendId)}`, { method: "DELETE" });
                        } catch {}
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

          {/* Right: logs panel */}
          <aside className="bg-subpanel rounded-2xl p-6 h-full flex flex-col justify-between">
            <div className="space-y-4 overflow-auto">
              {logs.map((l, idx) => (
                <div key={idx} className="h-14 rounded-full bg-chip px-4 flex items-center text-white/90">
                  {l}
                </div>
              ))}
            </div>
            <button className="mt-6 h-12 rounded-full btn-white px-6 text-lg font-medium self-start">View All</button>
          </aside>

          {/* Fullscreen overlay (layout-only) */}
          {isFullscreen && (
            <div className="fixed inset-0 bg-black/50 z-50 grid place-items-center p-6">
              <div className="relative w-full max-w-5xl h-[80dvh] rounded-2xl bg-tile-active text-black">
                <button
                  aria-label="Minimize stream"
                  onClick={() => setIsFullscreen(false)}
                  className="absolute right-4 top-4 h-8 w-8 grid place-items-center rounded-md bg-white/70 text-black hover:bg-white transition"
                  title="Minimize"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5">
                    <path d="M9 9L4 9L4 4M15 15L20 15L20 20" />
                    <path d="M9 9L4 4M15 15L20 20" />
                  </svg>
                </button>
                <div className="absolute bottom-4 right-4 text-[#4b4b4b] text-lg">
                  <StreamLabel label={streams[0]?.name || "stream"} />
                </div>
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
