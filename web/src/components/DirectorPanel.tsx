import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { CheckboxInput } from "@astryxdesign/core/CheckboxInput";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Divider } from "@astryxdesign/core/Divider";
import { Icon } from "@astryxdesign/core/Icon";
import { HStack, VStack } from "@astryxdesign/core/Layout";
import { List, ListItem } from "@astryxdesign/core/List";
import { Heading, Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { TextInput } from "@astryxdesign/core/TextInput";
import { ToggleButton } from "@astryxdesign/core/ToggleButton";
import { ChatBubbleLeftIcon, FilmIcon, PhotoIcon, SparklesIcon, UserIcon } from "@heroicons/react/24/outline";
import { useEffect, useState } from "react";
import { AgentsConfig, api, ProjectView, Senaryo } from "../api";

const VISUAL: Record<string, [typeof UserIcon, string]> = {
  "konuşmacı": [UserIcon, "Konuşmacı"],
  "b-roll": [PhotoIcon, "B-roll"],
  animasyon: [SparklesIcon, "Animasyon"],
  "yazı": [ChatBubbleLeftIcon, "Yazı"],
};

/** Senaristin çıkardığı özet ve sahne planı. */
function ScriptView({ s, brief }: { s: Senaryo; brief: Record<string, any> | null }) {
  const br = s.script?.brief ?? {};
  const beats = s.script?.beats ?? [];
  const tags = brief?.tone ? [brief.tone, `tempo: ${brief.pacing}`, `müzik: ${brief.music_mood}`] : [];
  return (
    <VStack gap={3}>
      <Heading level={4}>Senaryo</Heading>
      {br.logline && <Text>{br.logline}</Text>}
      {tags.length > 0 && (
        <HStack gap={1} wrap="wrap">
          {tags.map((t) => (
            <Badge key={t} label={t} variant="neutral" />
          ))}
        </HStack>
      )}
      {beats.length > 0 && (
        <List density="compact" hasDividers>
          {beats.map((b) => {
            const [icon, kind] = VISUAL[b.visual] ?? [FilmIcon, b.visual];
            return (
              <ListItem
                key={b.slot}
                startContent={<Icon icon={icon} color="secondary" />}
                label={b.idea || kind}
                description={[kind, b.purpose, b.template].filter(Boolean).join(" · ")}
              />
            );
          })}
        </List>
      )}
    </VStack>
  );
}

/** Yönetmen ajanı: stil talimatı, senaryo ve (gelişmiş) alt ajanların sistem prompt'ları. */
export function DirectorPanel({
  view,
  busy,
  runJob,
}: {
  view: ProjectView;
  busy: boolean;
  runJob: (p: Promise<{ id: string }>) => Promise<void>;
}) {
  const [cfg, setCfg] = useState<AgentsConfig | null>(null);
  const [style, setStyle] = useState("");
  const [brief, setBrief] = useState<Record<string, any> | null>(null);
  const [script, setScript] = useState<Senaryo | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.agents().then((c) => {
      setCfg(c);
      setStyle((s) => s || c.default_style || "");
    });
  }, []);
  useEffect(() => {
    api.brief(view.info.id).then(setBrief).catch(() => setBrief(null));
    api.senaryo(view.info.id).then(setScript).catch(() => setScript(null));
  }, [view.info.id, view.info.current_version]);

  if (!cfg) {
    return (
      <VStack padding={4}>
        <Text color="secondary">Yükleniyor…</Text>
      </VStack>
    );
  }

  const setAgent = (key: string, field: "system" | "model" | "think", value: string | boolean) => {
    setCfg({ ...cfg, agents: { ...cfg.agents, [key]: { ...cfg.agents[key], [field]: value } } });
    setDirty(true);
    setSaved(false);
  };

  const save = async () => {
    setCfg(await api.saveAgents(cfg));
    setDirty(false);
    setSaved(true);
  };

  return (
    <VStack gap={4} padding={4}>
      <VStack gap={3}>
        <Heading level={4}>Yönetmene talimat</Heading>
        <HStack gap={1} wrap="wrap">
          {Object.entries(cfg.style_presets || {}).map(([name, text]) => (
            <ToggleButton key={name} label={name} size="sm" isPressed={style === text} onPressedChange={() => setStyle(text)} />
          ))}
        </HStack>
        <TextArea label="Talimat" isLabelHidden rows={4} value={style} onChange={setStyle} placeholder="Nasıl bir video istiyorsun? Ton, tempo, görsel dil…" />
        <Button label="Senaryoyu yeniden yaz ve kurgula" variant="primary" width="100%" isDisabled={busy || !style.trim()} onClick={() => runJob(api.direct(view.info.id, style))} />
        <Text type="supporting" color="secondary">
          10-20 dk sürer · önceki hali Bilgi → Geri al ile döner
        </Text>
      </VStack>

      {script && (script.script || script.profile?.llm) && (
        <>
          <Divider />
          <ScriptView s={script} brief={brief} />
        </>
      )}

      <Divider />
      <Collapsible trigger={<Text color="secondary">Gelişmiş: ajan ayarları</Text>}>
        <VStack gap={3}>
          {Object.entries(cfg.agents).map(([key, a]) => (
            <Collapsible key={key} trigger={<Text weight="medium">{a.title || key}</Text>}>
              <VStack gap={2}>
                <TextArea
                  label="Sistem prompt'u"
                  description={a.system === "builtin" ? "Yerleşik prompt kullanılıyor; değiştirmek için yaz." : undefined}
                  rows={6}
                  value={a.system === "builtin" ? "" : a.system}
                  onChange={(v) => setAgent(key, "system", v || "builtin")}
                />
                <TextInput label="Model" description="Boş = varsayılan model" size="sm" value={a.model} onChange={(v) => setAgent(key, "model", v)} />
                <CheckboxInput label="Düşünme modu" value={!!a.think} onChange={(v) => setAgent(key, "think", v)} />
              </VStack>
            </Collapsible>
          ))}
          <Button label={saved && !dirty ? "Kaydedildi" : "Kaydet"} size="sm" isDisabled={!dirty} onClick={save} />
        </VStack>
      </Collapsible>
    </VStack>
  );
}
