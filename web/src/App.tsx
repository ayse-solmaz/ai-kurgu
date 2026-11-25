import { Badge } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { CheckboxInput } from "@astryxdesign/core/CheckboxInput";
import { EmptyState } from "@astryxdesign/core/EmptyState";
import { FileInput } from "@astryxdesign/core/FileInput";
import { Icon } from "@astryxdesign/core/Icon";
import { HStack, Layout, LayoutContent, LayoutHeader, LayoutPanel, StackItem, VStack } from "@astryxdesign/core/Layout";
import { List, ListItem } from "@astryxdesign/core/List";
import { ProgressBar } from "@astryxdesign/core/ProgressBar";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { Selector } from "@astryxdesign/core/Selector";
import { Tab, TabList } from "@astryxdesign/core/TabList";
import { Heading, Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { ArrowDownTrayIcon, FilmIcon, PauseIcon, PlayIcon, SparklesIcon } from "@heroicons/react/24/outline";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmt, JobInfo, media, ProjectInfo, ProjectView, STAGES, Timeline, watchJob } from "./api";
import { DirectorPanel } from "./components/DirectorPanel";
import { MotionPanel } from "./components/MotionPanel";
import { BRollPanel, ChatPanel, InfoPanel, StylePanel } from "./components/Panels";
import { Player, PlayerHandle } from "./components/Player";
import { TimelineView } from "./components/TimelineView";
import { TranscriptPanel } from "./components/TranscriptPanel";
import { TimeMap } from "./timemap";

type Tab = "director" | "transcript" | "chat" | "broll" | "motion" | "style" | "info";

export default function App() {
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(() => localStorageGet("kurgu.project"));
  const [view, setView] = useState<ProjectView | null>(null);
  const [job, setJob] = useState<Partial<JobInfo> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("director");
  const [renderName, setRenderName] = useState<string | null>(null);
  const [outT, setOutT] = useState(0);
  const [srcT, setSrcT] = useState(0);
  const [health, setHealth] = useState<any>(null);
  const player = useRef<PlayerHandle>(null);

  const refreshList = useCallback(() => api.projects().then(setProjects).catch((e) => setError(e.message)), []);
  const refresh = useCallback(async () => {
    if (!current) return setView(null);
    try {
      setView(await api.project(current));
    } catch (e: any) {
      setError(e.message);
      setCurrent(null);
    }
  }, [current]);

  useEffect(() => {
    refreshList();
    api.health().then(setHealth).catch(() => setHealth({ offline: true }));
  }, [refreshList]);
  useEffect(() => {
    localStorageSet("kurgu.project", current);
    setRenderName(null);
    refresh();
  }, [current, refresh]);

  // Sayfa yenilendiğinde süren işi takip etmeye devam et
  useEffect(() => {
    const active = view?.jobs?.find((j) => j.status === "running" || j.status === "queued");
    if (active && !job) follow(Promise.resolve(active));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.info.id]);

  const follow = useCallback(
    async (p: Promise<{ id: string }>) => {
      setError(null);
      try {
        const j = await p;
        setJob({ ...j, status: "running" } as JobInfo);
        const done = await watchJob(j.id, (u) => setJob((old) => ({ ...old, ...u })));
        setJob(null);
        await refresh();
        refreshList();
        if (done.kind?.startsWith("render") && done.result?.file) setRenderName(done.result.file);
        // tam otomatik kurgu bittiğinde son videoyu ve senaryoyu göster
        const auto = done.result?.report?.render?.file;
        if (done.kind === "pipeline" && auto) {
          setRenderName(auto);
          setTab("director");
        }
      } catch (e: any) {
        setJob(null);
        setError(e.message);
        refresh();
      }
    },
    [refresh, refreshList],
  );

  const tm = useMemo(() => new TimeMap(view?.keep ?? []), [view?.keep]);
  const onTime = useCallback((o: number, s: number) => {
    setOutT(o);
    setSrcT(s);
  }, []);
  const busy = !!job;

  const saveTimeline = async (tl: Timeline, note: string) => {
    if (!current) return;
    try {
      setView(await api.putTimeline(current, tl, note));
    } catch (e: any) {
      setError(e.message);
    }
  };

  const sidebar = (
    <Sidebar
      projects={projects}
      current={current}
      onSelect={setCurrent}
      onCreated={(id, jobP) => {
        setCurrent(id);
        refreshList();
        follow(jobP);
      }}
      health={health}
      busy={busy}
    />
  );

  if (!view) {
    return <Layout height="fill" start={sidebar} content={<LayoutContent padding={8}><Welcome health={health} /></LayoutContent>} />;
  }

  const header = (
    <LayoutHeader hasDivider padding={3}>
      <HStack gap={3} vAlign="center">
        <StackItem size="fill">
          <VStack gap={0.5}>
            <Text weight="semibold" maxLines={1}>
              {view.timeline?.meta.title || view.info.name}
            </Text>
            <Text type="supporting" color="secondary" maxLines={1}>
              {view.info.preset}
              {view.timeline ? ` · ${view.timeline.output.width}×${view.timeline.output.height}` : ""} · v{view.info.current_version}
              {view.output_duration ? ` · ${fmt(view.output_duration)}` : ""}
            </Text>
          </VStack>
        </StackItem>
        {view.renders.length > 0 && (
          <Selector
            label="Gösterilen video"
            isLabelHidden
            size="sm"
            width={200}
            value={renderName ?? "__live"}
            onChange={(v) => setRenderName(v === "__live" ? null : v)}
            options={[{ value: "__live", label: "Canlı düzenleme önizlemesi" }, ...view.renders.map((r) => ({ value: r.name, label: r.name }))]}
          />
        )}
        {renderName && <Button label="İndir" size="sm" variant="ghost" icon={<Icon icon={ArrowDownTrayIcon} />} href={media.render(view.info.id, renderName, true)} />}
        <Button
          label="Otomatik kurgu"
          size="sm"
          icon={<Icon icon={SparklesIcon} />}
          isDisabled={busy || !view.timeline}
          tooltip="Analiz araçları, senarist ve ajanlarla baştan kurgula"
          onClick={() => follow(api.direct(view.info.id, null))}
        />
        <Button label="Önizleme" size="sm" isDisabled={busy || !view.timeline} onClick={() => follow(api.render(view.info.id, true))} />
        <Button label="Final render" size="sm" variant="primary" isDisabled={busy || !view.timeline} onClick={() => follow(api.render(view.info.id, false))} />
      </HStack>
    </LayoutHeader>
  );

  const tabs: [Tab, string][] = [
    ["director", "Senaryo"],
    ["transcript", "Transkript"],
    ["broll", "B-roll"],
    ["motion", "Animasyon"],
    ["style", "Stil"],
    ["chat", "Sohbet"],
    ["info", "Bilgi"],
  ];
  const counts: Partial<Record<Tab, number>> = { broll: view.items?.brolls.length ?? 0, motion: view.items?.motions?.length ?? 0 };

  const side = (
    <LayoutPanel width={440} hasDivider padding={0} isScrollable={false} label="Düzenleme paneli">
      <Layout
        height="fill"
        header={
          <LayoutHeader padding={2}>
            <TabList value={tab} onChange={(v) => setTab(v as Tab)} size="sm" hasDivider isFullBleed>
              {tabs.map(([k, label]) => (
                <Tab key={k} value={k} label={label} endContent={counts[k] ? <Badge label={String(counts[k])} variant="neutral" /> : undefined} />
              ))}
            </TabList>
          </LayoutHeader>
        }
        content={
          <LayoutContent padding={0}>
            {tab === "transcript" && (
              <TranscriptPanel
                view={view}
                srcT={srcT}
                onSeekSrc={(t) => player.current?.seekSrc(t)}
                onCut={async (ids, restore) => {
                  try {
                    setView(await api.cutWords(view.info.id, ids, restore));
                  } catch (e: any) {
                    setError(e.message);
                  }
                }}
              />
            )}
            {tab === "director" && <DirectorPanel view={view} busy={busy} runJob={follow} />}
            {tab === "chat" && <ChatPanel view={view} onDone={refresh} busy={busy} />}
            {tab === "broll" && <BRollPanel view={view} outT={outT} onSeek={(t) => player.current?.seekOut(t)} onChange={saveTimeline} runJob={follow} />}
            {tab === "motion" && <MotionPanel view={view} outT={outT} busy={busy} onSeek={(t) => player.current?.seekOut(t)} onChange={saveTimeline} runJob={follow} />}
            {tab === "style" && (
              <StylePanel
                view={view}
                onChange={saveTimeline}
                onTheme={async (name) => {
                  try {
                    const r = await api.setTheme(view.info.id, name);
                    setView(r);
                    if (r.job) follow(Promise.resolve(r.job));
                  } catch (e: any) {
                    setError(e.message);
                  }
                }}
              />
            )}
            {tab === "info" && <InfoPanel view={view} onUndo={async () => setView(await api.undo(view.info.id))} onRedo={async () => setView(await api.redo(view.info.id))} />}
          </LayoutContent>
        }
      />
    </LayoutPanel>
  );

  const main = (
    <LayoutContent padding={3} isScrollable={false}>
      <VStack gap={3} height="100%">
        {job && <JobBar job={job} />}
        {error && <Banner status="error" title="Hata" description={error} isDismissable onDismiss={() => setError(null)} />}
        {view.timeline ? (
          <>
            <Card padding={3} height="100%" variant="muted">
              <Player ref={player} view={view} tm={tm} renderName={renderName} onTime={onTime} />
            </Card>
            <HStack gap={2} vAlign="center">
              <PlayButton onToggle={() => player.current?.toggle()} />
              <Text type="supporting" color="secondary" hasTabularNumbers>
                {fmt(outT)} / {fmt(renderName ? outT : tm.duration)}
              </Text>
              {!renderName && (
                <Text type="supporting" color="secondary">
                  Kesilen yerler canlı atlanıyor; altyazı ve b-roll yaklaşık önizleme
                </Text>
              )}
            </HStack>
            {!renderName && (
              <TimelineView view={view} tm={tm} outT={outT} onSeek={(t) => player.current?.seekOut(t)} onSelect={(k) => setTab(k === "brolls" ? "broll" : k === "motions" ? "motion" : tab)} />
            )}
          </>
        ) : (
          <EmptyState title={job ? "Video işleniyor…" : "Bu projede henüz kurgu planı yok"} icon={<Icon icon={FilmIcon} />} />
        )}
      </VStack>
    </LayoutContent>
  );

  return <Layout height="fill" start={sidebar} content={<Layout height="fill" header={header} content={main} end={side} />} />;
}

function PlayButton({ onToggle }: { onToggle: () => void }) {
  const [playing, setPlaying] = useState(false);
  return (
    <Button
      label={playing ? "Duraklat" : "Oynat"}
      size="sm"
      variant="ghost"
      isIconOnly
      icon={<Icon icon={playing ? PauseIcon : PlayIcon} />}
      onClick={() => {
        onToggle();
        setPlaying((p) => !p);
      }}
    />
  );
}

// ------------------------------------------------------------------ yan menü + yükleme

const PRESETS: [string, string, string][] = [
  ["shorts", "Shorts", "9:16 · en fazla 60 sn, en iyi bölüm seçilir"],
  ["dikey", "Dikey", "9:16 · videonun tamamı (Reels/TikTok)"],
  ["youtube", "YouTube", "16:9 · uzun video"],
];

function Sidebar({
  projects,
  current,
  onSelect,
  onCreated,
  health,
  busy,
}: {
  projects: ProjectInfo[];
  current: string | null;
  onSelect: (id: string) => void;
  onCreated: (id: string, job: Promise<{ id: string }>) => void;
  health: any;
  busy: boolean;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preset, setPreset] = useState("shorts");
  const [smart, setSmart] = useState(true);
  const [instruction, setInstruction] = useState("");
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!file) return;
    setUploading(true);
    setErr(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("preset", preset);
    fd.append("smart", String(smart));
    if (instruction) fd.append("instruction", instruction);
    try {
      const r = await api.create(fd);
      setFile(null);
      setInstruction("");
      onCreated(r.project.id, Promise.resolve(r.job));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <LayoutPanel width={288} hasDivider padding={0} isScrollable={false} label="Projeler">
      <Layout
        height="fill"
        header={
          <LayoutHeader padding={4}>
            <VStack gap={3}>
              <HStack gap={2} vAlign="center">
                <Icon icon={FilmIcon} color="accent" />
                <VStack gap={0}>
                  <Heading level={4}>AI Kurgu</Heading>
                  <Text type="supporting" color="secondary">
                    açık kaynak · yerel
                  </Text>
                </VStack>
              </HStack>
              <FileInput label="Video" isLabelHidden mode="dropzone" accept="video/*" placeholder="Videoyu sürükle veya seç" value={file} onChange={(f) => setFile(Array.isArray(f) ? f[0] ?? null : f)} />
              <SegmentedControl label="Format" value={preset} onChange={setPreset} size="sm" layout="fill">
                {PRESETS.map(([k, label]) => (
                  <SegmentedControlItem key={k} value={k} label={label} />
                ))}
              </SegmentedControl>
              <Text type="supporting" color="secondary">
                {PRESETS.find((p) => p[0] === preset)?.[2]}
              </Text>
              <CheckboxInput label="Tam otomatik kurgu" description="Senaryo, b-roll, animasyon, müzik ve final video" value={smart} onChange={setSmart} />
              {smart && <TextInput label="İstek" isLabelHidden size="sm" placeholder="İstek (isteğe bağlı): enerjik, eğitici…" value={instruction} onChange={setInstruction} />}
              <Button label={uploading ? "Yükleniyor…" : "Kurguya başla"} variant="primary" width="100%" isLoading={uploading} isDisabled={!file || busy} onClick={submit} />
              {err && <Banner status="error" title={err} />}
            </VStack>
          </LayoutHeader>
        }
        content={
          <LayoutContent padding={2}>
            <List density="balanced" hasDividers={false}>
              {projects.map((p) => (
                <ListItem key={p.id} label={p.name} description={`${p.preset} · ${p.status} · v${p.current_version}`} isSelected={current === p.id} onClick={() => onSelect(p.id)} />
              ))}
            </List>
          </LayoutContent>
        }
        footer={
          health && (
            <LayoutHeader hasDivider padding={3}>
              {health.offline ? (
                <Text type="supporting" color="secondary">
                  Sunucuya ulaşılamıyor
                </Text>
              ) : (
                <HStack gap={1} wrap="wrap">
                  <Badge label={`ffmpeg ${health.ffmpeg ? "✓" : "✗"}`} variant={health.ffmpeg ? "success" : "error"} />
                  <Badge label={`LLM ${health.models?.llm?.ready ? "✓" : "✗"}`} variant={health.models?.llm?.ready ? "success" : "error"} />
                  <Badge label={health.encoder ?? "encoder"} variant="neutral" />
                </HStack>
              )}
            </LayoutHeader>
          )
        }
      />
    </LayoutPanel>
  );
}

function JobBar({ job }: { job: Partial<JobInfo> }) {
  const pct = Math.round((job.progress ?? 0) * 100);
  const stage = STAGES[job.stage ?? ""] ?? job.stage ?? "Sırada";
  return (
    <Card padding={3}>
      <ProgressBar label={`${stage}${job.message ? ` · ${job.message}` : ""}`} value={pct} max={100} hasValueLabel />
    </Card>
  );
}

function Welcome({ health }: { health: any }) {
  return (
    <EmptyState
      icon={<Icon icon={FilmIcon} />}
      title="Videonu yükle, gerisini AI halletsin"
      description="Analiz araçları videonu betimler, senarist kurgu senaryosu yazar; ajanlar sessizlikleri ve tekrarları keser, altyazı, b-roll, animasyon ve müzik ekler. Hepsi bilgisayarında, açık kaynak modellerle."
      actions={health?.offline ? <Banner status="error" title="Sunucu kapalı. Terminalde: uv run kurgu serve" /> : undefined}
    />
  );
}

function localStorageGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}
function localStorageSet(k: string, v: string | null) {
  try {
    v ? localStorage.setItem(k, v) : localStorage.removeItem(k);
  } catch {
    /* yoksay */
  }
}
