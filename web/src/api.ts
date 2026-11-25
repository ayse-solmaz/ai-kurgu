// API istemcisi ve tipler

export type Span = { src: [number, number]; out: [number, number] | null } | null;

export interface Anchor {
  word_start: number | null;
  word_end: number | null;
  start: number | null;
  end: number | null;
}

export interface BRoll {
  id: string;
  anchor: Anchor;
  prompt: string;
  keywords: string[];
  mode: "full" | "pip";
  asset: string | null;
  provider: string | null;
  attribution: string | null;
  span: Span;
}

export interface TextItem {
  id: string;
  anchor: Anchor;
  text: string;
  style: "hook" | "title" | "lower_third" | "pop" | "chapter";
  position: "top" | "center" | "bottom";
  span: Span;
}

export interface ZoomItem {
  id: string;
  anchor: Anchor;
  scale: number;
  span: Span;
}

export interface MotionItem {
  id: string;
  anchor: Anchor;
  template: string;
  name: string;
  params: Record<string, any>;
  duration: number | null;
  layout: "card" | "full";
  asset: string | null;
  preview: string | null;
  hide_captions: boolean;
  span: Span;
}

export interface MotionTemplate {
  id: string;
  name: string;
  description: string;
  layout: "card" | "full";
  duration: { default?: number; min?: number; max?: number };
  params: Record<string, any>;
  example: Record<string, any>;
}

export interface Captions {
  enabled: boolean;
  style: "karaoke" | "classic" | "pop";
  position: "bottom" | "center" | "top";
  max_words: number;
  max_chars: number;
  uppercase: boolean;
  font: string;
  size: number;
  primary: string;
  highlight: string;
  outline_color: string;
  outline: number;
  shadow: number;
  margin_v: number;
  theme?: string;
  weight?: number;
  highlight_mode?: "color" | "pill" | "glow" | "none";
}

export interface ThemeInfo {
  id: string;
  label: string;
  font: string;
  highlight: string;
  highlight_mode: string;
}

export interface Beat {
  slot: number;
  sentence_id: number;
  phrase: string;
  purpose: string;
  visual: "konuşmacı" | "b-roll" | "animasyon" | "yazı";
  template: string;
  idea: string;
}

export interface Senaryo {
  script: { brief: Record<string, any>; beats: Beat[]; profile: Record<string, any> } | null;
  profile: Record<string, any>;
  scene: Record<string, any> | null;
}

export interface Timeline {
  version: number;
  preset: string;
  source: { path: string; duration: number; width: number; height: number; fps: number; has_audio: boolean };
  output: { width: number; height: number; fps: number };
  cuts: { start: number; end: number; reason: string; note: string | null }[];
  captions: Captions;
  music: { asset: string | null; mood: string | null; volume_db: number } | null;
  reframe: { enabled: boolean; track_faces: boolean; fill: string; keys: { t: number; cx: number }[] };
  meta: {
    title: string | null;
    summary: string | null;
    description: string | null;
    hook: string | null;
    hashtags: string[];
    chapters: { anchor: Anchor; title: string }[];
  };
  [k: string]: unknown;
}

export interface Word {
  id: number;
  text: string;
  start: number;
  end: number;
  cut: boolean;
  filler: boolean;
  stutter: boolean;
  retake: boolean;
}

export interface JobInfo {
  id: string;
  kind: string;
  project: string | null;
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  progress: number;
  message: string;
  result: any;
  error: string | null;
}

export interface ProjectInfo {
  id: string;
  name: string;
  created: string;
  preset: string;
  current_version: number;
  status: string;
}

export interface ProjectView {
  info: ProjectInfo;
  history: { version: number; note: string | null; current: boolean }[];
  jobs: JobInfo[];
  source: { duration: number; width: number; height: number; fps: number; has_audio: boolean } | null;
  has_proxy: boolean;
  renders: { name: string; size: number; mtime: number }[];
  timeline?: Timeline;
  keep?: [number, number][];
  output_duration?: number;
  items?: { brolls: BRoll[]; texts: TextItem[]; zooms: ZoomItem[]; voiceovers: any[]; motions: MotionItem[] };
  transcript?: { language: string; words: Word[]; sentences: { id: number; word_ids: number[] }[] };
  report?: { source_duration: number; output_duration: number; cuts: number; removed_by_reason: Record<string, number> };
}

export interface AgentDef {
  title: string;
  model: string;
  think: boolean;
  system: string;
}

export interface AgentsConfig {
  default_style: string;
  style_presets: Record<string, string>;
  agents: Record<string, AgentDef>;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const j = await r.json();
      msg = j.detail ?? JSON.stringify(j);
    } catch {
      /* yoksay */
    }
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  health: () => req<any>("GET", "/api/health"),
  providers: () => req<Record<string, string>>("GET", "/api/providers"),
  projects: () => req<ProjectInfo[]>("GET", "/api/projects"),
  project: (id: string) => req<ProjectView>("GET", `/api/projects/${id}`),
  create: (fd: FormData) => req<{ project: ProjectInfo; job: JobInfo }>("POST", "/api/projects", fd),
  remove: (id: string) => req("DELETE", `/api/projects/${id}`),
  cutWords: (id: string, word_ids: number[], restore: boolean) =>
    req<ProjectView>("POST", `/api/projects/${id}/words`, { word_ids, restore }),
  putTimeline: (id: string, tl: Timeline, note: string) =>
    req<ProjectView>("PUT", `/api/projects/${id}/timeline`, { ...tl, _note: note }),
  undo: (id: string) => req<ProjectView>("POST", `/api/projects/${id}/undo`),
  redo: (id: string) => req<ProjectView>("POST", `/api/projects/${id}/redo`),
  render: (id: string, preview: boolean) => req<JobInfo>("POST", `/api/projects/${id}/render`, { preview }),
  plan: (id: string, instruction: string | null) =>
    req<JobInfo>("POST", `/api/projects/${id}/plan`, { instruction, assets: true }),
  chat: (id: string, message: string, history: any[]) =>
    req<JobInfo>("POST", `/api/projects/${id}/chat`, { message, history }),
  agents: () => req<AgentsConfig>("GET", "/api/agents"),
  saveAgents: (cfg: AgentsConfig) => req<AgentsConfig>("PUT", "/api/agents", cfg),
  direct: (id: string, style: string | null) => req<JobInfo>("POST", `/api/projects/${id}/direct`, { style }),
  brief: (id: string) => req<Record<string, any>>("GET", `/api/projects/${id}/brief`),
  senaryo: (id: string) => req<Senaryo>("GET", `/api/projects/${id}/senaryo`),
  themes: () => req<ThemeInfo[]>("GET", "/api/themes"),
  setTheme: (id: string, name: string) => req<ProjectView & { job: JobInfo | null }>("POST", `/api/projects/${id}/theme`, { name }),
  motionTemplates: () => req<MotionTemplate[]>("GET", "/api/motion/templates"),
  motionPreview: (tid: string, params?: Record<string, any>) =>
    req<JobInfo>("POST", `/api/motion/templates/${tid}/preview`, params ? { params } : {}),
  animate: (id: string) => req<JobInfo>("POST", `/api/projects/${id}/animate`),
  renderMotions: (id: string) => req<JobInfo>("POST", `/api/projects/${id}/motions/render`),
  regen: (id: string, bid: string, prompt?: string) =>
    req<JobInfo>("POST", `/api/projects/${id}/brolls/${bid}/regenerate`, { prompt }),
  job: (jid: string) => req<JobInfo>("GET", `/api/jobs/${jid}`),
};

export const media = {
  proxy: (id: string) => `/api/projects/${id}/media/proxy`,
  source: (id: string) => `/api/projects/${id}/media/source`,
  render: (id: string, name: string, download = false) =>
    `/api/projects/${id}/renders/${encodeURIComponent(name)}${download ? "?download=true" : ""}`,
  motionPreview: (name: string) => `/api/motion/preview/${encodeURIComponent(name)}`,
  asset: (id: string, rel: string) => `/api/projects/${id}/assets/${rel.replace(/^assets\//, "assets/")}`,
};

/** İşi SSE ile izler; bitince çözülür. */
export function watchJob(jid: string, onUpdate: (j: Partial<JobInfo>) => void): Promise<JobInfo> {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/jobs/${jid}/events`);
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      onUpdate(ev);
      if (ev.type === "done" || ev.type === "failed") {
        es.close();
        api.job(jid).then((j) => (j.status === "done" ? resolve(j) : reject(new Error(j.error || "İş başarısız"))));
      }
    };
    es.onerror = () => {
      es.close();
      // bağlantı koptuysa durumu yoklayarak bekle
      const poll = async () => {
        try {
          const j = await api.job(jid);
          onUpdate(j);
          if (j.status === "done") resolve(j);
          else if (j.status === "failed") reject(new Error(j.error || "İş başarısız"));
          else setTimeout(poll, 1500);
        } catch (err) {
          reject(err);
        }
      };
      poll();
    };
  });
}

export const fmt = (t: number) => {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
};

export const STAGES: Record<string, string> = {
  ingest: "İçe alma",
  proxy: "Önizleme kopyası",
  asr: "Transkript",
  vad: "Sessizlik analizi",
  plan: "Senaryo ve kurgu",
  profile: "Video profili",
  music: "Müzik",
  faces: "Yüz takibi",
  assets: "B-roll üretimi",
  audio: "Ses",
  video: "Görüntü",
  agent: "Ajan",
  done: "Bitti",
};
