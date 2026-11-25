// AI Kurgu masaüstü uygulaması (Electron ana süreci)
//
// Görevleri:
//  1. Python motorunu (FastAPI, `kurgu serve`) boş bir portta başlatmak
//  2. Hazır olunca arayüzü kendi penceresinde açmak
//  3. Kurulu sürümde ilk açılışta Python ortamını uv ile hazırlamak
//  4. Uygulama kapanınca motoru da kapatmak
//
// Geliştirme modu (npm start): depo kökündeki kaynaklar ve .venv kullanılır.
// Kurulu mod: resources/backend içindeki kaynaklar; ortam ve veriler %APPDATA%/AI Kurgu altında.

const { app, BrowserWindow, Menu, dialog, shell, ipcMain } = require("electron");
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");
const http = require("http");

const isPackaged = app.isPackaged;
const repoRoot = path.resolve(__dirname, "..");
const backendRoot = isPackaged ? path.join(process.resourcesPath, "backend") : repoRoot;
const userData = app.getPath("userData");
// Projeler, modeller ve ayarlar: geliştirmede depo kökü, kurulu sürümde kullanıcı klasörü
const dataRoot = isPackaged ? path.join(userData, "data") : repoRoot;
const logFile = path.join(userData, "backend.log");

let backend = null;
let mainWindow = null;
let splash = null;
let quitting = false;

// ------------------------------------------------------------------ yardımcılar

function log(line) {
  fs.mkdirSync(userData, { recursive: true });
  fs.appendFileSync(logFile, `[${new Date().toISOString()}] ${line}\n`);
}

function status(text, detail = "") {
  log(`durum: ${text} ${detail}`);
  if (splash && !splash.isDestroyed()) splash.webContents.send("status", { text, detail });
}

function freePort(preferred = 8765) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", () => {
      const s2 = net.createServer();
      s2.listen(0, "127.0.0.1", () => {
        const { port } = s2.address();
        s2.close(() => resolve(port));
      });
    });
    srv.listen(preferred, "127.0.0.1", () => srv.close(() => resolve(preferred)));
  });
}

function waitForHealth(port, timeoutMs = 120000) {
  const t0 = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get({ host: "127.0.0.1", port, path: "/api/presets", timeout: 3000 }, (res) => {
        res.resume();
        if (res.statusCode === 200) return resolve();
        retry();
      });
      req.on("error", retry);
      req.on("timeout", () => req.destroy());
    };
    const retry = () => {
      if (quitting) return reject(new Error("kapanıyor"));
      if (backend && backend.exitCode !== null) return reject(new Error("Motor beklenmedik şekilde kapandı. Ayrıntı: " + logFile));
      if (Date.now() - t0 > timeoutMs) return reject(new Error("Motor zamanında başlamadı. Ayrıntı: " + logFile));
      setTimeout(tryOnce, 500);
    };
    tryOnce();
  });
}

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    log(`çalıştır: ${cmd} ${args.join(" ")}`);
    const p = spawn(cmd, args, { windowsHide: true, ...opts });
    p.stdout?.on("data", (d) => {
      const s = d.toString();
      log(s.trim());
      opts.onLine?.(s);
    });
    p.stderr?.on("data", (d) => {
      const s = d.toString();
      log(s.trim());
      opts.onLine?.(s);
    });
    p.on("error", reject);
    p.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${path.basename(cmd)} çıkış kodu ${code}`))));
  });
}

// ------------------------------------------------------------------ ilk kurulum (kurulu sürüm)

function copyDefaults() {
  // Düzenlenebilir ayar dosyaları ilk açılışta kullanıcı klasörüne kopyalanır
  fs.mkdirSync(dataRoot, { recursive: true });
  for (const item of ["config.yaml", "models_registry.yaml", "agents.yaml", "presets", "assets"]) {
    const src = path.join(backendRoot, item);
    const dst = path.join(dataRoot, item);
    if (fs.existsSync(src) && !fs.existsSync(dst)) fs.cpSync(src, dst, { recursive: true });
  }
  // Uygulamanın kendi dosyaları (animasyon şablonları, fontlar, ses efektleri, yardımcı betikler) her sürüm
  // güncellemesinde yenilenir. Kullanıcının eklediği şablon/greenscreen klasörleri silinmez (yalnızca üzerine yazılır).
  const verFile = path.join(dataRoot, ".app_version");
  const fresh = !fs.existsSync(verFile) || fs.readFileSync(verFile, "utf8").trim() !== app.getVersion();
  for (const item of ["motion", "scripts", path.join("assets", "fonts"), path.join("assets", "sfx")]) {
    const src = path.join(backendRoot, item);
    const dst = path.join(dataRoot, item);
    if (fs.existsSync(src) && (fresh || !fs.existsSync(dst))) fs.cpSync(src, dst, { recursive: true, force: true });
  }
  // Sürüm güncellemesi: kullanıcı modeli değiştirmediyse (eski varsayılan qwen3:8b) yeni varsayılana geç.
  // Model indirilmemişse arka uç otomatik olarak qwen3:8b'ye düşer.
  const userCfg = path.join(dataRoot, "config.yaml");
  if (fresh && fs.existsSync(userCfg)) {
    const txt = fs.readFileSync(userCfg, "utf8");
    const next = txt.replace(/^(\s*llm:\s*)qwen3:8b\s*$/m, "$1qwen3:30b-a3b");
    if (next !== txt) fs.writeFileSync(userCfg, next);
  }
  fs.writeFileSync(verFile, app.getVersion());
}

function uvEnv() {
  return {
    ...process.env,
    UV_PROJECT_ENVIRONMENT: path.join(userData, "venv"),
    UV_PYTHON_INSTALL_DIR: path.join(userData, "python"),
    // uv'nin varsayılan kullanıcı önbelleği kullanılır (güncellemelerde yeniden indirme olmaz)
  };
}

async function ensureEnvironment() {
  if (!isPackaged) return;
  copyDefaults();
  const marker = path.join(userData, "venv", ".kurgu-version");
  const version = app.getVersion();
  if (fs.existsSync(marker) && fs.readFileSync(marker, "utf8").trim() === version) return;
  const uv = path.join(process.resourcesPath, "backend", "bin", "uv.exe");
  status("İlk kurulum: Python ortamı hazırlanıyor", "Bu işlem bir kez yapılır ve internet hızına göre birkaç dakika sürebilir.");
  // --no-bin / --no-registry: kullanıcının genel Python'una (~/.local/bin, Windows kayıt defteri) dokunma
  const pyArgs = ["python", "install", "3.12", "--no-bin", "--no-registry"];
  const pyOpts = { env: uvEnv(), onLine: (s) => status("Python 3.12 indiriliyor", s.trim().slice(-90)) };
  const pyDir = path.join(userData, "python");
  const installedPython = () => {
    // uv'nin "cpython-3.12.14-..." klasörü (kısayol/junction olan "cpython-3.12-..." değil)
    try {
      for (const d of fs.readdirSync(pyDir)) {
        const exe = path.join(pyDir, d, "python.exe");
        if (/^cpython-3\.12\.\d+-windows/.test(d) && fs.existsSync(exe)) return exe;
      }
    } catch {}
    return null;
  };
  try {
    await run(uv, pyArgs, pyOpts);
  } catch (e) {
    // uv 0.12, bazı klasörlerde Python'u kurduktan sonra "minor version link" adımında hata veriyor
    // ("Missing expected target directory ..."). Python yine de kurulmuş olur: python.exe varsa onunla devam et.
    if (!installedPython()) throw e;
  }
  const py = installedPython();
  await run(uv, ["sync", "--frozen", "--no-dev", "--project", backendRoot, ...(py ? ["--python", py] : [])], {
    env: uvEnv(),
    onLine: (s) => status("Kütüphaneler kuruluyor", s.trim().slice(-90)),
  });
  fs.writeFileSync(marker, version);
}

function hasCommand(name) {
  return new Promise((resolve) => execFile("where", [name], { windowsHide: true }, (err) => resolve(!err)));
}

function wingetFfmpeg() {
  const pkgs = path.join(process.env.LOCALAPPDATA || "", "Microsoft", "WinGet", "Packages");
  try {
    return fs.readdirSync(pkgs).some((d) => d.startsWith("Gyan.FFmpeg"));
  } catch {
    return false;
  }
}

async function ensureTools() {
  // ffmpeg: render için şart. Yoksa kullanıcının onayıyla winget üzerinden kurulur.
  if (!(await hasCommand("ffmpeg")) && !wingetFfmpeg()) {
    const r = dialog.showMessageBoxSync({
      type: "question",
      buttons: ["Kur", "Şimdi değil"],
      title: "ffmpeg gerekli",
      message: "Video işlemek için ffmpeg gerekiyor. Windows Paket Yöneticisi (winget) ile kurulsun mu?",
    });
    if (r === 0) {
      status("ffmpeg kuruluyor", "winget install Gyan.FFmpeg");
      await run("winget", ["install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements"])
        .catch((e) => log(`ffmpeg kurulamadı: ${e}`));
    }
  }
  // Ollama: akıllı plan ve sohbet için. Yoksa uygulama yine açılır, kullanıcı bilgilendirilir.
  const ollamaExe = path.join(process.env.LOCALAPPDATA || "", "Programs", "Ollama", "ollama.exe");
  if (!(await hasCommand("ollama")) && !fs.existsSync(ollamaExe)) {
    const r = dialog.showMessageBoxSync({
      type: "info",
      buttons: ["İndirme sayfasını aç", "Devam et"],
      title: "Ollama bulunamadı",
      message: "Akıllı plan ve sohbet özellikleri için ücretsiz Ollama gerekiyor. Temizlik, altyazı ve render onsuz da çalışır.",
    });
    if (r === 0) shell.openExternal("https://ollama.com/download");
  }
}

function backendCommand(port) {
  const args = ["serve", "--host", "127.0.0.1", "--port", String(port)];
  if (isPackaged) {
    const exe = path.join(userData, "venv", "Scripts", "kurgu.exe");
    return { cmd: exe, args };
  }
  const devExe = path.join(repoRoot, ".venv", "Scripts", "kurgu.exe");
  if (fs.existsSync(devExe)) return { cmd: devExe, args };
  return { cmd: "uv", args: ["run", "--no-sync", "kurgu", ...args] };
}

async function startBackend() {
  await ensureEnvironment();
  if (isPackaged) await ensureTools();
  const port = await freePort(isPackaged ? 8766 : 8765);
  const { cmd, args } = backendCommand(port);
  status("Motor başlatılıyor", `127.0.0.1:${port}`);
  const env = {
    ...process.env,
    KURGU_ROOT: dataRoot,
    KURGU_WEB_DIST: path.join(backendRoot, "web", "dist"),
    // Hareketli grafik render'ı için: motor bu Electron'u --render-motion ile çağırır
    KURGU_ELECTRON: process.execPath,
    KURGU_ELECTRON_APP: isPackaged ? "" : __dirname,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  backend = spawn(cmd, args, { cwd: dataRoot, env, windowsHide: true });
  backend.stdout.on("data", (d) => log(d.toString().trim()));
  backend.stderr.on("data", (d) => log(d.toString().trim()));
  backend.on("exit", (code) => {
    log(`motor kapandı (${code})`);
    if (!quitting && mainWindow) {
      dialog.showErrorBox("AI Kurgu", `Motor beklenmedik şekilde kapandı (kod ${code}).\nAyrıntı: ${logFile}`);
    }
  });
  await waitForHealth(port);
  return port;
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  // Windows'ta alt süreçlerle (ffmpeg, Ollama çağrıları) birlikte kapat
  if (process.platform === "win32") execFile("taskkill", ["/pid", String(backend.pid), "/T", "/F"], () => {});
  else backend.kill("SIGTERM");
}

// ------------------------------------------------------------------ pencereler

function createSplash() {
  splash = new BrowserWindow({
    width: 480,
    height: 300,
    frame: false,
    resizable: false,
    backgroundColor: "#0e1116",
    show: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true },
  });
  splash.loadFile(path.join(__dirname, "splash.html"));
}

function createMainWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    backgroundColor: "#0e1116",
    title: "AI Kurgu",
    icon: path.join(__dirname, "icon.png"),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  const origin = `http://127.0.0.1:${port}`;
  mainWindow.loadURL(origin);

  // Uygulama dışındaki bağlantılar varsayılan tarayıcıda açılsın
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(origin)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(origin)) {
      e.preventDefault();
      shell.openExternal(url);
    }
  });

  // "İndir" düğmesi: kayıt yeri sorulsun
  mainWindow.webContents.session.on("will-download", (_e, item) => {
    const target = dialog.showSaveDialogSync(mainWindow, {
      defaultPath: path.join(app.getPath("videos"), item.getFilename()),
      filters: [{ name: "Video", extensions: ["mp4"] }, { name: "Tümü", extensions: ["*"] }],
    });
    if (target) item.setSavePath(target);
    else item.cancel();
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (splash && !splash.isDestroyed()) splash.close();
  });
  mainWindow.on("closed", () => (mainWindow = null));
}

function buildMenu() {
  const template = [
    {
      label: "Dosya",
      submenu: [
        { label: "Projeler klasörünü aç", click: () => shell.openPath(path.join(dataRoot, "projects")) },
        { label: "Ayarlar (config.yaml)", click: () => shell.openPath(path.join(dataRoot, "config.yaml")) },
        { label: "Motor günlüğü", click: () => shell.openPath(logFile) },
        { type: "separator" },
        { role: "quit", label: "Çıkış" },
      ],
    },
    {
      label: "Görünüm",
      submenu: [
        { role: "reload", label: "Yenile" },
        { role: "toggleDevTools", label: "Geliştirici araçları" },
        { type: "separator" },
        { role: "resetZoom", label: "Gerçek boyut" },
        { role: "zoomIn", label: "Yakınlaştır" },
        { role: "zoomOut", label: "Uzaklaştır" },
        { role: "togglefullscreen", label: "Tam ekran" },
      ],
    },
    {
      label: "Yardım",
      submenu: [
        { label: "Lisanslar", click: () => shell.openPath(path.join(backendRoot, "LICENSES.md")) },
        { label: "Ollama'yı indir", click: () => shell.openExternal("https://ollama.com/download") },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ------------------------------------------------------------------ yaşam döngüsü

const renderArg = process.argv.indexOf("--render-motion");

if (renderArg !== -1) {
  // Render modu: pencere/motor açmadan hareketli grafik karelerini üret ve çık.
  // Ana uygulama açıkken de çalışabilmesi için tek örnek kilidi alınmaz.
  app.commandLine.appendSwitch("force-device-scale-factor", "1");
  app.commandLine.appendSwitch("disable-gpu-vsync");
  app.whenReady()
    .then(() => require("./motion-render").run(process.argv[renderArg + 1]))
    .then(() => app.exit(0))
    .catch((err) => {
      console.error(err && err.stack ? err.stack : err);
      app.exit(1);
    });
} else if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    buildMenu();
    createSplash();
    try {
      const port = await startBackend();
      status("Arayüz açılıyor");
      createMainWindow(port);
    } catch (err) {
      log(`hata: ${err.stack || err}`);
      dialog.showErrorBox("AI Kurgu başlatılamadı", `${err.message}\n\nGünlük: ${logFile}`);
      app.quit();
    }
  });

  app.on("before-quit", () => {
    quitting = true;
    stopBackend();
  });
  app.on("window-all-closed", () => app.quit());
}

ipcMain.handle("app-info", () => ({ version: app.getVersion(), packaged: isPackaged, dataRoot }));
