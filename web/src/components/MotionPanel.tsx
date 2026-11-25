import { Badge } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { ClickableCard } from "@astryxdesign/core/ClickableCard";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Grid } from "@astryxdesign/core/Grid";
import { Icon } from "@astryxdesign/core/Icon";
import { HStack, StackItem, VStack } from "@astryxdesign/core/Layout";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { Heading, Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { PlayIcon, SparklesIcon } from "@heroicons/react/24/outline";
import { useEffect, useState } from "react";
import { api, fmt, media, MotionItem, MotionTemplate, ProjectView, Timeline, watchJob } from "../api";
import { TimeMap } from "../timemap";

/** Animasyonlar: otomatik ekleme, eklenenleri düzenleme ve şablon galerisi. */
export function MotionPanel({
  view,
  outT,
  busy,
  onSeek,
  onChange,
  runJob,
}: {
  view: ProjectView;
  outT: number;
  busy: boolean;
  onSeek: (t: number) => void;
  onChange: (tl: Timeline, note: string) => Promise<void>;
  runJob: (p: Promise<{ id: string }>) => Promise<void>;
}) {
  const [templates, setTemplates] = useState<MotionTemplate[]>([]);
  const [mode, setMode] = useState("list");
  const items = view.items?.motions ?? [];

  useEffect(() => {
    api.motionTemplates().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  const tl = view.timeline!;
  const rawMotions = (tl.motions as any[]) ?? [];

  const saveParams = async (m: MotionItem, params: Record<string, any>) => {
    const next = rawMotions.map((x) => (x.id === m.id ? { ...x, params, asset: null, preview: null } : x));
    await onChange({ ...tl, motions: next }, `${m.id} parametreleri değişti`);
    await runJob(api.renderMotions(view.info.id));
  };

  const remove = (m: MotionItem) => onChange({ ...tl, motions: rawMotions.filter((x) => x.id !== m.id) }, `${m.id} animasyonu silindi`);

  const addAtCursor = async (t: MotionTemplate) => {
    const tm = new TimeMap(view.keep!);
    const ids = new Set(rawMotions.map((x) => x.id));
    let n = 1;
    while (ids.has(`m${n}`)) n++;
    const src = tm.toSrc(outT);
    const item = { id: `m${n}`, anchor: { word_start: null, word_end: null, start: src, end: src + 0.1 }, template: t.id, params: t.example, duration: null, layout: t.layout, asset: null, preview: null, icons: [] };
    await onChange({ ...tl, motions: [...rawMotions, item] }, `${t.name} eklendi (${fmt(outT)})`);
    await runJob(api.renderMotions(view.info.id));
  };

  return (
    <VStack gap={3} padding={4}>
      <HStack gap={2} vAlign="center">
        <StackItem size="fill">
          <SegmentedControl label="Görünüm" value={mode} onChange={setMode} size="sm">
            <SegmentedControlItem value="list" label={`Videodakiler (${items.length})`} />
            <SegmentedControlItem value="gallery" label={`Şablonlar (${templates.length})`} />
          </SegmentedControl>
        </StackItem>
        <Button label="Otomatik ekle" size="sm" icon={<Icon icon={SparklesIcon} />} isDisabled={busy} onClick={() => runJob(api.animate(view.info.id))} />
      </HStack>

      {mode === "gallery" ? (
        <Grid columns={2} gap={2}>
          {templates.map((t) => (
            <TemplateCard key={t.id} t={t} outT={outT} onAdd={() => addAtCursor(t)} busy={busy} />
          ))}
        </Grid>
      ) : items.length === 0 ? (
        <Text color="secondary">Henüz animasyon yok. "Otomatik ekle" konuşmadaki sayıları, süreçleri ve listeleri bulup uygun şablonları yerleştirir.</Text>
      ) : (
        items.map((m) => <MotionCard key={m.id} view={view} m={m} onSeek={onSeek} onSave={saveParams} onRemove={() => remove(m)} busy={busy} />)
      )}
    </VStack>
  );
}

function MotionCard({
  view,
  m,
  onSeek,
  onSave,
  onRemove,
  busy,
}: {
  view: ProjectView;
  m: MotionItem;
  onSeek: (t: number) => void;
  busy: boolean;
  onSave: (m: MotionItem, p: Record<string, any>) => Promise<void>;
  onRemove: () => void;
}) {
  const [text, setText] = useState(JSON.stringify(m.params, null, 2));
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setText(JSON.stringify(m.params, null, 2));
  }, [m.params]);
  const dirty = text !== JSON.stringify(m.params, null, 2);

  return (
    <Card padding={3}>
      <VStack gap={2}>
        <HStack gap={3}>
          <ClickableCard label={`${m.id} konumuna git`} padding={0} width={64} variant="muted" onClick={() => m.span?.out && onSeek(m.span.out[0])}>
            {m.preview ? (
              <video src={media.asset(view.info.id, m.preview)} muted loop autoPlay playsInline className="aspect-[9/16] w-full object-contain" />
            ) : (
              <VStack padding={2}>
                <Text type="supporting" color="secondary">
                  render yok
                </Text>
              </VStack>
            )}
          </ClickableCard>
          <StackItem size="fill">
            <VStack gap={1}>
              <HStack gap={2} vAlign="center">
                <Text weight="semibold">{m.name}</Text>
                <Badge label={m.layout === "full" ? "tam ekran" : "kart"} variant="neutral" />
              </HStack>
              <Text type="supporting" color="secondary">
                {m.span?.out ? `${fmt(m.span.out[0])} – ${fmt(m.span.out[1])}` : "kesik bölgede"}
              </Text>
              <Text type="supporting" maxLines={2}>
                {summary(m.params)}
              </Text>
              <HStack gap={1}>
                <Button label="Sil" size="sm" variant="ghost" onClick={onRemove} />
              </HStack>
            </VStack>
          </StackItem>
        </HStack>
        <Collapsible trigger={<Text type="supporting" color="secondary">Parametreleri düzenle</Text>}>
          <VStack gap={2}>
            <TextArea
              label="Parametreler (JSON)"
              isLabelHidden
              rows={8}
              value={text}
              onChange={(v) => {
                setText(v);
                setErr(null);
              }}
            />
            {err && <Banner status="error" title={err} />}
            <Button
              label="Kaydet ve yeniden render et"
              size="sm"
              variant="primary"
              isDisabled={!dirty || busy}
              onClick={async () => {
                try {
                  await onSave(m, JSON.parse(text));
                } catch (e: any) {
                  setErr(`Geçersiz JSON: ${e.message}`);
                }
              }}
            />
          </VStack>
        </Collapsible>
      </VStack>
    </Card>
  );
}

function TemplateCard({ t, outT, onAdd, busy }: { t: MotionTemplate; outT: number; onAdd: () => void; busy: boolean }) {
  const [webm, setWebm] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const preview = async () => {
    setLoading(true);
    try {
      const job = await api.motionPreview(t.id);
      const done = await watchJob(job.id, () => {});
      if (done.result?.webm) setWebm(media.motionPreview(done.result.webm));
    } finally {
      setLoading(false);
    }
  };
  return (
    <Card padding={2}>
      <VStack gap={2}>
        {webm ? (
          <video src={webm} muted loop autoPlay playsInline className="aspect-[9/16] w-full rounded-md bg-black object-contain" />
        ) : (
          <Button label={loading ? "Render ediliyor…" : "Önizle"} size="sm" variant="ghost" icon={<Icon icon={PlayIcon} />} isLoading={loading} onClick={preview} />
        )}
        <Heading level={5}>{t.name}</Heading>
        <Text type="supporting" color="secondary" maxLines={3}>
          {t.description}
        </Text>
        <Button label={`${fmt(outT)} anına ekle`} size="sm" isDisabled={busy} onClick={onAdd} />
      </VStack>
    </Card>
  );
}

function summary(p: Record<string, any>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(p)) {
    if (k === "position" || k.endsWith("_svg")) continue;
    if (Array.isArray(v)) parts.push(v.map((x) => (typeof x === "object" ? x.label ?? x.text ?? x.when : x)).join(" · "));
    else if (typeof v !== "object") parts.push(String(v));
  }
  return parts.join(" | ");
}
