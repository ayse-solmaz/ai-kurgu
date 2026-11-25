import { Button } from "@astryxdesign/core/Button";
import { VStack } from "@astryxdesign/core/Layout";
import { Text } from "@astryxdesign/core/Text";
import { Toolbar } from "@astryxdesign/core/Toolbar";
import { useMemo, useState } from "react";
import { ProjectView } from "../api";

interface Props {
  view: ProjectView;
  srcT: number;
  onSeekSrc: (t: number) => void;
  onCut: (ids: number[], restore: boolean) => Promise<void>;
}

export function TranscriptPanel({ view, srcT, onSeekSrc, onCut }: Props) {
  const tr = view.transcript;
  const [sel, setSel] = useState<number[]>([]);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const words = tr?.words ?? [];
  const playing = useMemo(() => words.find((w) => srcT >= w.start && srcT <= w.end)?.id, [words, srcT]);

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

  const act = async (restore: boolean) => {
    setBusy(true);
    try {
      await onCut(sel, restore);
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
          <Text type="supporting" color="secondary">
            Tıkla: git · Shift: aralık · Ctrl: çoklu
          </Text>
        }
        endContent={
          <>
            <Button label="Kes" size="sm" isDisabled={!selKept || busy} onClick={() => act(false)} />
            <Button label="Geri getir" size="sm" variant="ghost" isDisabled={!selCut || busy} onClick={() => act(true)} />
          </>
        }
      />
      <VStack gap={3} padding={4} isScrollable>
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
    </VStack>
  );
}
