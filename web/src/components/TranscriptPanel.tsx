import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { VStack } from "@astryxdesign/core/Layout";
import { List, ListItem } from "@astryxdesign/core/List";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { Text } from "@astryxdesign/core/Text";
import { Toolbar } from "@astryxdesign/core/Toolbar";
import { useMemo, useState } from "react";
import { fmt, ProjectView, Word } from "../api";

interface Props {
  view: ProjectView;
  srcT: number;
  onSeekSrc: (t: number) => void;
  onCut: (ids: number[], restore: boolean) => Promise<void>;
}

const REASON: Record<string, [string, "warning" | "info" | "neutral" | "purple"]> = {
  retake: ["Tekrar çekim", "warning"],
  filler: ["Dolgu (ııı/eee)", "info"],
  stutter: ["Kekeleme", "info"],
  unworded: ["Anlamsız ses", "neutral"],
  manual: ["Elle", "purple"],
  trim: ["Seçilen bölüm dışı", "neutral"],
};

interface CutGroup {
  reason: string;
  start: number;
  end: number;
  words: Word[];
}

/** Aynı nedenli bitişik kesimleri birleştirir ve her grubun içindeki kelimeleri bulur (sessizlikler hariç). */
function cutGroups(view: ProjectView): CutGroup[] {
  const words = view.transcript?.words ?? [];
  const cuts = [...(view.timeline?.cuts ?? [])].filter((c) => c.reason !== "silence").sort((a, b) => a.start - b.start);
  const groups: CutGroup[] = [];
  for (const c of cuts) {
    const last = groups.filter((g) => g.reason === c.reason).at(-1);
    if (last && c.start - last.end < 0.4) last.end = Math.max(last.end, c.end);
    else groups.push({ reason: c.reason, start: c.start, end: c.end, words: [] });
  }
  for (const g of groups) g.words = words.filter((w) => w.start >= g.start - 0.05 && w.end <= g.end + 0.05);
  return groups.filter((g) => g.words.length > 0).sort((a, b) => a.start - b.start);
}

export function TranscriptPanel({ view, srcT, onSeekSrc, onCut }: Props) {
  const tr = view.transcript;
  const [sel, setSel] = useState<number[]>([]);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("text");
  const words = tr?.words ?? [];
  const playing = useMemo(() => words.find((w) => srcT >= w.start && srcT <= w.end)?.id, [words, srcT]);
  const groups = useMemo(() => cutGroups(view), [view]);
  const reviewable = groups.filter((g) => g.reason !== "trim");

  if (!tr) {
    return (
      <VStack padding={4}>
        <Text color="secondary">Transkript henüz hazır değil.</Text>
      </VStack>
    );
  }

  const click = (id: number, e: React.MouseEvent) => {
    if (e.shiftKey && anchor !== null) {
      const [a, b] = [Math.min(anchor, id), Math.max(anchor, id)];
      setSel(Array.from({ length: b - a + 1 }, (_, i) => a + i));
    } else if (e.ctrlKey || e.metaKey) {
      setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
      setAnchor(id);
    } else {
      setSel([id]);
      setAnchor(id);
      if (!words[id].cut) onSeekSrc(words[id].start);
    }
  };

  const act = async (ids: number[], restore: boolean) => {
    setBusy(true);
    try {
      await onCut(ids, restore);
      setSel([]);
    } finally {
      setBusy(false);
    }
  };

  const selCut = sel.some((i) => words[i]?.cut);
  const selKept = sel.some((i) => !words[i]?.cut);

  return (
    <VStack gap={0} height="100%">
      <Toolbar
        label="Transkript araçları"
        size="sm"
        dividers={["bottom"]}
        startContent={
          <SegmentedControl label="Görünüm" value={mode} onChange={setMode} size="sm">
            <SegmentedControlItem value="text" label="Metin" />
            <SegmentedControlItem value="cuts" label={`Kesimler (${reviewable.length})`} />
          </SegmentedControl>
        }
        endContent={
          mode === "text" ? (
            <>
              <Button label="Kes" size="sm" isDisabled={!selKept || busy} onClick={() => act(sel, false)} />
              <Button label="Geri getir" size="sm" variant="ghost" isDisabled={!selCut || busy} onClick={() => act(sel, true)} />
            </>
          ) : undefined
        }
      />
      {mode === "cuts" ? (
        <VStack gap={2} padding={4} isScrollable>
          <Text type="supporting" color="secondary">
            Uygulamanın kestiği yerler. Tıklayınca o ana gider; yanlış kesildiyse "Geri al" ile videoya geri döner. Sessizlik kesimleri listelenmez.
          </Text>
          {reviewable.length === 0 ? (
            <Text color="secondary">Konuşmadan kesilen yer yok.</Text>
          ) : (
            <List density="compact" hasDividers>
              {reviewable.map((g) => {
                const [label, variant] = REASON[g.reason] ?? [g.reason, "neutral"];
                const text = g.words.map((w) => w.text).join(" ");
                return (
                  <ListItem
                    key={`${g.reason}-${g.start}`}
                    onClick={() => onSeekSrc(Math.max(0, g.start - 1))}
                    label={text.length > 140 ? text.slice(0, 140) + "…" : text}
                    description={`${fmt(g.start)} · ${(g.end - g.start).toFixed(1)} sn`}
                    startContent={<Badge label={label} variant={variant} />}
                    endContent={
                      <Button
                        label="Geri al"
                        size="sm"
                        variant="ghost"
                        isDisabled={busy}
                        onClick={(e: React.MouseEvent) => {
                          e.stopPropagation();
                          act(
                            g.words.map((w) => w.id),
                            true,
                          );
                        }}
                      />
                    }
                  />
                );
              })}
            </List>
          )}
        </VStack>
      ) : (
        <VStack gap={3} padding={4} isScrollable>
          <Text type="supporting" color="secondary">
            Tıkla: git · Shift: aralık seç · Ctrl: çoklu seç · üstü çizili: kesildi
          </Text>
          {tr.sentences.map((s) => (
            <Text key={s.id} as="p" size="lg">
              {s.word_ids.map((id) => {
                const w = words[id];
                const tag = w.filler ? "dolgu" : w.stutter ? "kekeleme" : w.retake ? "tekrar" : "";
                return (
                  <span
                    key={id}
                    onClick={(e) => click(id, e)}
                    title={`${w.start.toFixed(2)}s${tag ? " · " + tag : ""}`}
                    className={
                      "cursor-pointer select-none rounded px-0.5 transition-colors " +
                      (sel.includes(id) ? "bg-accent/30 " : "") +
                      (w.cut ? "text-cut/70 line-through " : "hover:bg-white/10 ") +
                      (playing === id ? "bg-accent text-white " : "") +
                      (tag && !w.cut ? "underline decoration-dotted decoration-amber-400 " : "")
                    }
                  >
                    {w.text}{" "}
                  </span>
                );
              })}
            </Text>
          ))}
        </VStack>
      )}
    </VStack>
  );
}
