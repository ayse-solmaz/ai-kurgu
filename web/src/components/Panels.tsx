import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { ChatComposer, ChatLayout, ChatMessage, ChatMessageList, ChatSystemMessage } from "@astryxdesign/core/Chat";
import { CheckboxInput } from "@astryxdesign/core/CheckboxInput";
import { ClickableCard } from "@astryxdesign/core/ClickableCard";
import { Divider } from "@astryxdesign/core/Divider";
import { HStack, StackItem, VStack } from "@astryxdesign/core/Layout";
import { List, ListItem } from "@astryxdesign/core/List";
import { Selector } from "@astryxdesign/core/Selector";
import { Slider } from "@astryxdesign/core/Slider";
import { Heading, Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { TextInput } from "@astryxdesign/core/TextInput";
import { useEffect, useState } from "react";
import { api, BRoll, Captions, fmt, media, ProjectView, ThemeInfo, Timeline, watchJob } from "../api";

// ------------------------------------------------------------------ Sohbet

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  actions?: string[];
}

export function ChatPanel({ view, onDone, busy }: { view: ProjectView; onDone: () => void; busy: boolean }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  const send = async (message: string) => {
    if (!message.trim()) return;
    setMsgs((m) => [...m, { role: "user", content: message }]);
    setText("");
    setStatus("Ajan düşünüyor…");
    try {
      const job = await api.chat(view.info.id, message, history);
      const done = await watchJob(job.id, (j) => j.message && setStatus(j.message));
      const r = done.result;
      setHistory(r.history);
      setMsgs((m) => [...m, { role: "assistant", content: r.reply, actions: r.actions }]);
      onDone();
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", content: `Hata: ${e.message}` }]);
    } finally {
      setStatus(null);
    }
  };

  const examples = ["İlk 3 saniyeyi kes", "Altyazıları daha büyük yap", "Galaksi kısmına uzay görseli ekle", "Daha enerjik olsun, daha fazla zoom ekle"];

  return (
    <ChatLayout
      composer={
        <ChatComposer value={text} onChange={setText} onSubmit={send} placeholder="Ör: 0:20'ye kara delik görseli koy" isDisabled={!!status || busy} />
      }
    >
      <ChatMessageList
        emptyState={
        <VStack gap={2} padding={4}>
          <Text color="secondary">Kurguyu yazarak düzenle. Örnekler:</Text>
          {examples.map((e) => (
            <Button key={e} label={e} variant="ghost" size="sm" isDisabled={busy || !!status} onClick={() => send(e)} />
          ))}
        </VStack>
        }
      >
        {msgs.length > 0 || status
          ? [
              ...msgs.map((m, i) => (
                <ChatMessage key={i} sender={m.role}>
                  <VStack gap={1}>
                    <Text>{m.content}</Text>
                    {m.actions?.map((a, j) => (
                      <Text key={j} type="supporting" color="secondary">
                        • {a}
                      </Text>
                    ))}
                  </VStack>
                </ChatMessage>
              )),
              status ? <ChatSystemMessage key="status">{status}</ChatSystemMessage> : null,
            ]
          : null}
      </ChatMessageList>
    </ChatLayout>
  );
}

// ------------------------------------------------------------------ B-roll

export function BRollPanel({
  view,
  outT,
  onSeek,
  onChange,
  runJob,
}: {
  view: ProjectView;
  outT: number;
  onSeek: (t: number) => void;
  onChange: (tl: Timeline, note: string) => Promise<void>;
  runJob: (p: Promise<{ id: string }>) => Promise<void>;
}) {
  const items = view.items!.brolls;
  const [newPrompt, setNewPrompt] = useState("");

  const remove = (b: BRoll) => {
    const tl = view.timeline!;
    onChange({ ...tl, brolls: (tl.brolls as any[]).filter((x) => x.id !== b.id) }, `${b.id} silindi`);
  };

  const add = async () => {
    if (!newPrompt.trim()) return;
    const tl = view.timeline!;
    const ids = new Set((tl.brolls as any[]).map((b) => b.id));
    let n = 1;
    while (ids.has(`b${n}`)) n++;
    // çıktı zamanını kaynak zamana çevir (sunucu kelimeye bağlamaz, saniyeyle saklar)
    const { TimeMap } = await import("../timemap");
    const tm = new TimeMap(view.keep!);
    const a = tm.toSrc(outT);
    const b = tm.toSrc(Math.min(outT + 3, tm.duration));
    const br = { id: `b${n}`, anchor: { word_start: null, word_end: null, start: a, end: b }, prompt: newPrompt, keywords: [], kind: "any", motion: "kenburns", mode: "full", asset: null, provider: null, attribution: null };
    await onChange({ ...tl, brolls: [...(tl.brolls as any[]), br] }, `b${n} eklendi`);
    setNewPrompt("");
    await runJob(api.regen(view.info.id, `b${n}`));
  };

  return (
    <VStack gap={3} padding={4}>
      <HStack gap={2} vAlign="end">
        <StackItem size="fill">
          <TextInput label={`Şu ana (${fmt(outT)}) b-roll ekle`} size="sm" placeholder="İngilizce tarif: aerial view of istanbul at sunset" value={newPrompt} onChange={setNewPrompt} onEnter={add} />
        </StackItem>
        <Button label="Ekle" size="sm" variant="primary" isDisabled={!newPrompt.trim()} onClick={add} />
      </HStack>
      {items.length === 0 && <Text color="secondary">Henüz b-roll yok. Otomatik kurgu uygun yerlere ekler.</Text>}
      {items.map((b) => (
        <BRollCard key={b.id} view={view} b={b} onSeek={onSeek} onRemove={() => remove(b)} runJob={runJob} />
      ))}
    </VStack>
  );
}

function BRollCard({ view, b, onSeek, onRemove, runJob }: { view: ProjectView; b: BRoll; onSeek: (t: number) => void; onRemove: () => void; runJob: (p: Promise<{ id: string }>) => Promise<void> }) {
  const [prompt, setPrompt] = useState(b.prompt);
  useEffect(() => {
    setPrompt(b.prompt);
  }, [b.prompt]);
  return (
    <Card padding={3}>
      <HStack gap={3}>
        <ClickableCard label={`${b.id} konumuna git`} padding={0} width={112} onClick={() => b.span?.out && onSeek(b.span.out[0])}>
          {b.asset ? (
            <video src={media.asset(view.info.id, b.asset)} muted loop playsInline className="aspect-video w-full object-cover" onMouseEnter={(e) => e.currentTarget.play()} onMouseLeave={(e) => e.currentTarget.pause()} />
          ) : (
            <VStack padding={4}>
              <Text type="supporting" color="secondary">
                üretilmedi
              </Text>
            </VStack>
          )}
        </ClickableCard>
        <StackItem size="fill">
          <VStack gap={2}>
            <HStack gap={2} vAlign="center">
              <Badge label={b.id} variant="cyan" />
              <Text type="supporting" color="secondary">
                {b.span?.out ? `${fmt(b.span.out[0])} – ${fmt(b.span.out[1])}` : "kesik bölgede"}
              </Text>
            </HStack>
            <TextArea label="Görsel tarifi" isLabelHidden rows={2} value={prompt} onChange={setPrompt} />
            {b.attribution && (
              <Text type="supporting" color="secondary" maxLines={1}>
                {b.attribution}
              </Text>
            )}
            <HStack gap={1}>
              <Button label="Yeniden üret" size="sm" onClick={() => runJob(api.regen(view.info.id, b.id, prompt !== b.prompt ? prompt : undefined))} />
              <Button label="Sil" size="sm" variant="ghost" onClick={onRemove} />
            </HStack>
          </VStack>
        </StackItem>
      </HStack>
    </Card>
  );
}

// ------------------------------------------------------------------ Stil

const FONTS = ["Inter", "Inter Tight", "Montserrat", "Arial Black", "Arial", "Impact", "Segoe UI Black", "Verdana", "Georgia"];

export function StylePanel({
  view,
  onChange,
  onTheme,
}: {
  view: ProjectView;
  onChange: (tl: Timeline, note: string) => Promise<void>;
  onTheme: (name: string) => Promise<void>;
}) {
  const tl = view.timeline!;
  const [c, setC] = useState<Captions>(tl.captions);
  const [themes, setThemes] = useState<ThemeInfo[]>([]);
  const [applying, setApplying] = useState<string | null>(null);
  useEffect(() => {
    setC(tl.captions);
  }, [tl.captions]);
  useEffect(() => {
    api.themes().then(setThemes).catch(() => {});
  }, []);
  const set = <K extends keyof Captions>(k: K, v: Captions[K]) => setC((x) => ({ ...x, [k]: v }));
  const dirty = JSON.stringify(c) !== JSON.stringify(tl.captions);
  const current = (tl.theme as string) || c.theme || "apple";

  return (
    <VStack gap={4} padding={4}>
      <VStack gap={2}>
        <Heading level={4}>Tema</Heading>
        <Text type="supporting" color="secondary">
          Altyazı ve animasyonlar birlikte değişir; animasyonlar arka planda yeniden üretilir.
        </Text>
        <List density="compact" hasDividers={false}>
          {themes.map((t) => (
            <ListItem
              key={t.id}
              label={t.label}
              description={`${t.font}${applying === t.id ? " · uygulanıyor…" : ""}`}
              isSelected={current === t.id}
              isDisabled={!!applying}
              onClick={async () => {
                setApplying(t.id);
                try {
                  await onTheme(t.id);
                } finally {
                  setApplying(null);
                }
              }}
            />
          ))}
        </List>
      </VStack>
      <Divider />
      <VStack gap={3}>
        <Heading level={4}>Altyazı</Heading>
        <CheckboxInput label="Altyazı açık" value={c.enabled} onChange={(v) => set("enabled", v)} />
        <VStack gap={3}>
          <Selector
            label="Stil"
            size="sm"
            value={c.style}
            onChange={(v) => set("style", v as Captions["style"])}
            options={[
              { value: "karaoke", label: "Karaoke" },
              { value: "pop", label: "Pop" },
              { value: "classic", label: "Klasik" },
            ]}
          />
          <Selector
            label="Konum"
            size="sm"
            value={c.position}
            onChange={(v) => set("position", v as Captions["position"])}
            options={[
              { value: "center", label: "Orta-alt" },
              { value: "bottom", label: "Alt" },
              { value: "top", label: "Üst" },
            ]}
          />
          <Selector label="Yazı tipi" size="sm" value={c.font} onChange={(v) => set("font", v)} options={FONTS.map((f) => ({ value: f, label: f }))} />
          <Slider label="Boyut" min={28} max={140} value={c.size} onChange={(v: number) => set("size", v)} valueDisplay="text" />
          <Slider label="Kelime / satır" min={1} max={10} value={c.max_words} onChange={(v: number) => set("max_words", v)} valueDisplay="text" />
          <Slider label="Kontur" min={0} max={12} value={c.outline} onChange={(v: number) => set("outline", v)} valueDisplay="text" />
          <ColorField label="Yazı rengi" value={c.primary} onChange={(v) => set("primary", v)} />
          <ColorField label="Vurgu rengi" value={c.highlight} onChange={(v) => set("highlight", v)} />
        </VStack>
        <CheckboxInput label="Büyük harf" value={c.uppercase} onChange={(v) => set("uppercase", v)} />
        <Button label="Uygula" variant="primary" width="100%" isDisabled={!dirty} onClick={() => onChange({ ...tl, captions: c }, "Altyazı stili değişti")} />
      </VStack>
    </VStack>
  );
}

/** Astryx'te renk seçici yok: yerel renk girişi + onaltılık değer alanı. */
function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <HStack gap={2} vAlign="end">
      <input type="color" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className="h-8 w-10 cursor-pointer rounded-md border-0 bg-transparent" />
      <StackItem size="fill">
        <TextInput label={label} size="sm" value={value} onChange={onChange} />
      </StackItem>
    </HStack>
  );
}

// ------------------------------------------------------------------ Bilgi + geçmiş

export function InfoPanel({ view, onUndo, onRedo }: { view: ProjectView; onUndo: () => void; onRedo: () => void }) {
  const tl = view.timeline!;
  const m = tl.meta;
  const [providers, setProviders] = useState<Record<string, string> | null>(null);
  useEffect(() => {
    api.providers().then(setProviders).catch(() => {});
  }, []);
  const rep = view.report;
  const names: Record<string, string> = { silence: "Sessizlik", filler: "Dolgu (ııı/eee)", stutter: "Kekeleme", retake: "Tekrar çekim", unworded: "Anlamsız ses", trim: "Baş/son", manual: "Elle" };
  const attributions = [...new Set(view.items!.brolls.map((b) => b.attribution).filter(Boolean))];
  const copy = (t: string) => navigator.clipboard.writeText(t);

  return (
    <VStack gap={4} padding={4}>
      {rep && (
        <VStack gap={2}>
          <Heading level={4}>Süre</Heading>
          <Text>
            {fmt(rep.source_duration)} → <Text weight="semibold">{fmt(rep.output_duration)}</Text>
          </Text>
          <List density="compact" hasDividers={false}>
            {Object.entries(rep.removed_by_reason).map(([k, v]) => (
              <ListItem key={k} label={names[k] ?? k} endContent={<Text color="secondary">−{v.toFixed(1)} sn</Text>} />
            ))}
          </List>
        </VStack>
      )}
      {(m.title || m.hook) && (
        <>
          <Divider />
          <VStack gap={3}>
            {m.title && <Field label="Başlık" value={m.title} onCopy={copy} />}
            {m.hook && <Field label="Hook" value={m.hook} onCopy={copy} />}
            {m.description && <Field label="Açıklama" value={m.description + (m.hashtags.length ? "\n\n" + m.hashtags.join(" ") : "")} onCopy={copy} />}
            {m.summary && <Field label="Özet" value={m.summary} onCopy={copy} />}
          </VStack>
        </>
      )}
      {attributions.length > 0 && (
        <VStack gap={1}>
          <Heading level={5}>Stok içerik atıfları</Heading>
          {attributions.map((a) => (
            <Text key={a} type="supporting" color="secondary">
              {a}
            </Text>
          ))}
        </VStack>
      )}
      <Divider />
      <VStack gap={2}>
        <HStack gap={1} vAlign="center">
          <StackItem size="fill">
            <Heading level={4}>Sürüm geçmişi</Heading>
          </StackItem>
          <Button label="Geri al" size="sm" variant="ghost" onClick={onUndo} />
          <Button label="Yinele" size="sm" variant="ghost" onClick={onRedo} />
        </HStack>
        <List density="compact" hasDividers={false}>
          {[...view.history].reverse().map((h) => (
            <ListItem key={h.version} label={`v${h.version}`} description={h.note} isSelected={h.current} />
          ))}
        </List>
      </VStack>
      {providers && (
        <VStack gap={1}>
          <Heading level={5}>B-roll sağlayıcıları</Heading>
          <HStack gap={1} wrap="wrap">
            {Object.entries(providers).map(([k, v]) => (
              <Badge key={k} label={`${k}: ${v}`} variant={v === "hazır" ? "success" : "warning"} />
            ))}
          </HStack>
        </VStack>
      )}
    </VStack>
  );
}

function Field({ label, value, onCopy }: { label: string; value: string; onCopy: (t: string) => void }) {
  return (
    <VStack gap={1}>
      <HStack gap={1} vAlign="center">
        <StackItem size="fill">
          <Text type="label" color="secondary">
            {label}
          </Text>
        </StackItem>
        <Button label="Kopyala" size="sm" variant="ghost" onClick={() => onCopy(value)} />
      </HStack>
      <Text>{value}</Text>
    </VStack>
  );
}
