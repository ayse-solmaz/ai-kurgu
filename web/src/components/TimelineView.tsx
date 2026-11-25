import { Card } from "@astryxdesign/core/Card";
import { useRef } from "react";
import { fmt, ProjectView } from "../api";
import { TimeMap } from "../timemap";

interface Props {
  view: ProjectView;
  tm: TimeMap;
  outT: number;
  onSeek: (t: number) => void;
  onSelect?: (kind: string, id: string) => void;
}

const TRACKS = [
  { key: "brolls", label: "B-roll", color: "bg-broll" },
  { key: "texts", label: "Yazı", color: "bg-text" },
  { key: "zooms", label: "Zoom", color: "bg-zoom" },
  { key: "motions", label: "Animasyon", color: "bg-pink-400" },
] as const;

export function TimelineView({ view, tm, outT, onSeek, onSelect }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const D = Math.max(tm.duration, 0.1);
  const pct = (t: number) => `${(t / D) * 100}%`;
  const items = view.items!;

  const seekFromEvent = (e: React.MouseEvent) => {
    const r = box.current!.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(D, ((e.clientX - r.left) / r.width) * D)));
  };

  const ticks: number[] = [];
  const step = D > 600 ? 60 : D > 120 ? 15 : D > 30 ? 5 : 1;
  for (let t = 0; t <= D; t += step) ticks.push(t);

  return (
    <Card padding={3}>
      <div className="select-none">
      <div className="flex">
        <div className="w-16 shrink-0" />
        <div className="relative h-4 flex-1 text-[10px] text-muted">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2" style={{ left: pct(t) }}>
              {fmt(t).replace(/\.\d$/, "")}
            </span>
          ))}
        </div>
      </div>
      <div className="flex">
        <div className="w-16 shrink-0 space-y-1.5 pt-0.5 text-[11px] text-muted">
          <div className="h-5 leading-5">Konuşma</div>
          {TRACKS.map((t) => (
            <div key={t.key} className="h-5 leading-5">
              {t.label}
            </div>
          ))}
        </div>
        <div ref={box} className="relative flex-1 cursor-pointer space-y-1.5" onMouseDown={seekFromEvent}>
          {/* konuşma şeridi: tutulan parçalar, kesim noktaları kırmızı çizgi */}
          <div className="relative h-5 overflow-hidden rounded-md bg-black/25">
            {tm.spans.map(([a, b, o]) => (
              <div key={o} className="absolute top-0 h-full bg-slate-500/40" style={{ left: pct(o), width: pct(b - a) }} />
            ))}
            {tm.cutPoints().map((c) => (
              <div key={c} className="absolute top-0 h-full w-px bg-cut/80" style={{ left: pct(c) }} title="kesim" />
            ))}
          </div>
          {TRACKS.map((track) => (
            <div key={track.key} className="relative h-5 rounded-md bg-black/25">
              {(items[track.key] as any[]).map((it) =>
                it.span?.out ? (
                  <div
                    key={it.id}
                    title={it.prompt ?? it.text ?? it.id}
                    onMouseDown={(e) => {
                      e.stopPropagation();
                      onSeek(it.span.out[0]);
                      onSelect?.(track.key, it.id);
                    }}
                    className={`absolute top-0.5 h-4 truncate rounded-[5px] px-1 text-[10px] font-medium leading-4 text-black/80 ${track.color} ${it.asset === null && track.key === "brolls" ? "opacity-50 outline-1 outline-dashed outline-white/60" : ""}`}
                    style={{ left: pct(it.span.out[0]), width: `max(6px, ${pct(it.span.out[1] - it.span.out[0])})` }}
                  >
                    {it.id} {it.text ?? it.name ?? ""}
                  </div>
                ) : null,
              )}
            </div>
          ))}
          <div className="pointer-events-none absolute -top-1 bottom-0 w-0.5 bg-accent" style={{ left: pct(outT) }}>
            <div className="absolute -left-1 -top-1 h-2.5 w-2.5 rounded-full bg-accent" />
          </div>
        </div>
      </div>
      </div>
    </Card>
  );
}
