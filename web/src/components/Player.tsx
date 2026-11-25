import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { media, ProjectView, Word } from "../api";
import { TimeMap } from "../timemap";

export interface PlayerHandle {
  seekOut: (t: number) => void;
  seekSrc: (t: number) => void;
  toggle: () => void;
}

interface Props {
  view: ProjectView;
  tm: TimeMap;
  renderName: string | null; // doluysa render edilmiş dosyayı oynatır
  onTime: (outT: number, srcT: number) => void;
}

/** Altyazı gruplarını backend'e benzer şekilde oluşturur (önizleme amaçlı). */
function captionGroups(words: Word[], tm: TimeMap, maxWords: number, maxChars: number) {
  const vis = words
    .filter((w) => !w.cut)
    .map((w) => ({ ...w, os: tm.toOut(w.start) ?? tm.toOut((w.start + w.end) / 2) ?? 0, oe: tm.toOut(w.end) ?? 0 }));
  const groups: (typeof vis)[] = [];
  let cur: typeof vis = [];
  let chars = 0;
  for (const w of vis) {
    const prev = cur[cur.length - 1];
    if (
      prev &&
      (cur.length >= maxWords || chars + w.text.length + 1 > maxChars || w.os - prev.oe > 0.6 || /[.?!…:]$/.test(prev.text))
    ) {
      groups.push(cur);
      cur = [];
      chars = 0;
    }
    cur.push(w);
    chars += w.text.length + 1;
  }
  if (cur.length) groups.push(cur);
  return groups;
}

const trUpper = (s: string) => s.replace(/i/g, "İ").replace(/ı/g, "I").toUpperCase();

export const Player = forwardRef<PlayerHandle, Props>(function Player({ view, tm, renderName, onTime }, ref) {
  const video = useRef<HTMLVideoElement>(null);
  const [srcT, setSrcT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const tl = view.timeline!;
  const editMode = !renderName;
  const outT = editMode ? tm.toOut(srcT) ?? 0 : srcT;

  useImperativeHandle(ref, () => ({
    seekOut: (t) => {
      if (!video.current) return;
      video.current.currentTime = editMode ? tm.toSrc(t) : t;
    },
    seekSrc: (t) => {
      if (video.current && editMode) video.current.currentTime = t;
    },
    toggle: () => {
      const v = video.current;
      if (!v) return;
      v.paused ? v.play() : v.pause();
    },
  }));

  // Kesilen bölgeleri canlı atla
  useEffect(() => {
    let raf = 0;
    let lastReport = -1;
    const tick = () => {
      const v = video.current;
      if (v) {
        let t = v.currentTime;
        if (editMode) {
          const nk = tm.nextKeep(t);
          if (nk === null) {
            if (!v.paused) v.pause();
          } else if (nk - t > 0.03) {
            v.currentTime = nk;
            t = nk;
          }
        }
        setSrcT(t);
        // üst bileşeni saniyede ~12 kez güncelle (uzun transkriptlerde akıcılık için)
        if (Math.abs(t - lastReport) > 0.08) {
          lastReport = t;
          onTime(editMode ? tm.toOut(t) ?? 0 : t, t);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [tm, editMode, onTime]);

  const groups = useMemo(
    () => (view.transcript ? captionGroups(view.transcript.words, tm, tl.captions.max_words, tl.captions.max_chars) : []),
    [view.transcript, tm, tl.captions.max_words, tl.captions.max_chars],
  );

  const aspect = tl.output.width / tl.output.height;
  const srcAspect = tl.source.width / tl.source.height;
  const items = view.items!;
  const inSpan = (s: { out: [number, number] | null } | null) => !!s?.out && outT >= s.out[0] && outT <= s.out[1];

  // Önizleme overlay'leri (sadece düzenleme modunda; render dosyasında zaten gömülü)
  const broll = editMode ? items.brolls.find((b) => b.asset && inSpan(b.span)) : undefined;
  const texts = editMode ? items.texts.filter((t) => inSpan(t.span)) : [];
  const zoom = editMode ? items.zooms.find((z) => inSpan(z.span)) : undefined;
  const motion = editMode ? (items.motions ?? []).find((m) => m.preview && inSpan(m.span)) : undefined;
  const group = editMode && tl.captions.enabled && !motion?.hide_captions ? groups.find((g) => outT >= g[0].os - 0.05 && outT <= g[g.length - 1].oe + 0.35) : undefined;

  // Yüz takipli kırpma: object-position ile canlı taklit
  let objPos = "50% 50%";
  if (tl.reframe.enabled && tl.reframe.keys.length) {
    const k = tl.reframe.keys.reduce((best, k) => (Math.abs(k.t - srcT) < Math.abs(best.t - srcT) ? k : best));
    objPos = `${(k.cx * 100).toFixed(1)}% 50%`;
  }
  const fit = editMode && Math.abs(aspect - srcAspect) > 0.01 && !tl.reframe.enabled ? "contain" : "cover";
  const brollRef = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const b = brollRef.current;
    if (b && broll?.span?.out) {
      const want = outT - broll.span.out[0];
      if (Math.abs(b.currentTime - want) > 0.3) b.currentTime = Math.max(0, want);
      if (playing && b.paused) b.play().catch(() => {});
      if (!playing && !b.paused) b.pause();
    }
  }, [broll?.id, outT, playing]);

  const capPos = tl.captions.position === "center" ? "bottom-[27%]" : tl.captions.position === "top" ? "top-[8%]" : "bottom-[6%]";
  const capScale = 100 / tl.output.height; // piksel -> yükseklik yüzdesi (cqh)

  return (
    <div className="flex h-full w-full items-center justify-center">
      <div
        className="relative overflow-hidden rounded-2xl bg-black shadow-[0_20px_60px_rgba(0,0,0,0.6),0_0_0_1px_rgba(255,255,255,0.08)]"
        style={{ aspectRatio: `${tl.output.width} / ${tl.output.height}`, height: aspect < 1 ? "100%" : undefined, width: aspect >= 1 ? "100%" : undefined, maxHeight: "100%", maxWidth: "100%", containerType: "size" }}
      >
        {fit === "contain" && <div className="absolute inset-0 bg-gradient-to-b from-zinc-800 to-zinc-950" />}
        <video
          ref={video}
          key={renderName ?? "edit"}
          src={renderName ? media.render(view.info.id, renderName) : view.has_proxy ? media.proxy(view.info.id) : media.source(view.info.id)}
          className="absolute inset-0 h-full w-full transition-transform duration-150"
          style={{ objectFit: renderName ? "contain" : fit, objectPosition: objPos, transform: zoom ? `scale(${zoom.scale})` : undefined }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onClick={() => (video.current?.paused ? video.current.play() : video.current?.pause())}
          playsInline
        />
        {broll?.asset && (
          <video
            ref={brollRef}
            key={broll.id + broll.asset}
            src={media.asset(view.info.id, broll.asset)}
            muted
            playsInline
            className={broll.mode === "pip" ? "absolute right-[5%] top-[8%] w-[45%] rounded-lg border-2 border-white/80 shadow-xl" : "absolute inset-0 h-full w-full object-cover"}
          />
        )}
        {motion?.preview && (
          <MotionOverlay key={motion.id + motion.preview} src={media.asset(view.info.id, motion.preview)} offset={outT - motion.span!.out![0]} playing={playing} />
        )}
        {texts.map((t) => (
          <div
            key={t.id}
            className={
              "pointer-events-none absolute left-1/2 -translate-x-1/2 text-center font-black " +
              (t.style === "hook"
                ? "top-[12%] rounded-md bg-accent px-3 py-1 text-white"
                : t.style === "lower_third"
                  ? "bottom-[12%] left-[5%] translate-x-0 rounded bg-black/70 px-3 py-1 text-left text-white"
                  : t.position === "center"
                    ? "top-[45%] text-accent-2 drop-shadow-[0_3px_3px_rgba(0,0,0,0.9)]"
                    : "top-[22%] text-accent-2 drop-shadow-[0_3px_3px_rgba(0,0,0,0.9)]")
            }
            style={{ fontSize: t.style === "lower_third" ? "4.5cqmin" : "7cqmin", maxWidth: "86%" }}
          >
            {tl.captions.uppercase && (t.style === "hook" || t.style === "pop") ? trUpper(t.text) : t.text}
          </div>
        ))}
        {group && (
          <div
            className={`pointer-events-none absolute left-[7%] right-[7%] text-center leading-tight ${capPos}`}
            style={{
              fontFamily: tl.captions.font,
              fontWeight: 900,
              fontSize: `${tl.captions.size * capScale}cqh`,
              color: tl.captions.primary,
              WebkitTextStroke: `${Math.max(1, tl.captions.outline * capScale * 2)}px ${tl.captions.outline_color}`,
              paintOrder: "stroke fill",
              textShadow: "0 3px 6px rgba(0,0,0,0.6)",
            }}
          >
            {group.map((w, i) => {
              const active = tl.captions.style === "karaoke" && outT >= w.os && (i === group.length - 1 || outT < group[i + 1].os);
              const txt = tl.captions.uppercase ? trUpper(w.text) : w.text;
              return (
                <span key={w.id} style={active ? { color: tl.captions.highlight, display: "inline-block", transform: "scale(1.08)" } : undefined}>
                  {txt}
                  {i < group.length - 1 ? " " : ""}
                </span>
              );
            })}
          </div>
        )}
        {!playing && (
          <button
            onClick={() => video.current?.play()}
            className="absolute inset-0 m-auto flex h-16 w-16 items-center justify-center rounded-full border border-white/25 bg-white/15 text-3xl text-white shadow-[0_8px_32px_rgba(0,0,0,0.4)] backdrop-blur-xl transition hover:scale-105 hover:bg-white/25"
            aria-label="Oynat"
          >
            ▶
          </button>
        )}
        {renderName && (
          <div className="absolute left-2 top-2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-white">Render: {renderName}</div>
        )}
      </div>
    </div>
  );
});

/** Animasyon önizlemesi (alfa kanallı webm), oynatıcıyla senkron. */
function MotionOverlay({ src, offset, playing }: { src: string; offset: number; playing: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (Math.abs(v.currentTime - offset) > 0.25) v.currentTime = Math.max(0, offset);
    if (playing && v.paused) v.play().catch(() => {});
    if (!playing && !v.paused) v.pause();
  }, [offset, playing]);
  return <video ref={ref} src={src} muted playsInline className="pointer-events-none absolute inset-0 h-full w-full object-contain" />;
}
