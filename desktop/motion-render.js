// Hareketli grafik şablonlarını kare kare, şeffaf arka planla PNG'ye render eder.
//
// Kullanım (Python motoru çağırır):
//   electron <desktop klasörü> --render-motion job.json
//   "AI Kurgu.exe" --render-motion job.json        (kurulu sürüm)
//
// job.json: { html, width, height, fps, duration, out_dir }
// Görünmez (offscreen) bir Chromium penceresi açılır; her kare için sayfadaki
// window.__kurguSeek(t) çağrılır ve 'paint' olayındaki görüntü PNG olarak kaydedilir.

const { BrowserWindow } = require("electron");
const fs = require("fs");
const path = require("path");

async function run(jobPath) {
  const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));
  const { width, height, fps, duration, out_dir: outDir } = job;
  fs.mkdirSync(outDir, { recursive: true });

  const win = new BrowserWindow({
    show: false,
    width,
    height,
    useContentSize: true,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    webPreferences: { offscreen: true, sandbox: true, contextIsolation: true, backgroundThrottling: false },
  });
  win.webContents.setFrameRate(fps);
  win.setContentSize(width, height);

  let lastImage = null;
  let waiter = null;
  win.webContents.on("paint", (_e, _dirty, image) => {
    lastImage = image;
    if (waiter) {
      const w = waiter;
      waiter = null;
      w(image);
    }
  });
  const nextPaint = (timeoutMs = 1500) =>
    new Promise((resolve) => {
      waiter = resolve;
      win.webContents.invalidate();
      setTimeout(() => {
        if (waiter === resolve) {
          waiter = null;
          resolve(lastImage); // sayfa değişmediyse son kare
        }
      }, timeoutMs);
    });

  await win.loadFile(job.html);
  await win.webContents.executeJavaScript("window.__kurguReady");

  const total = Math.max(1, Math.round(duration * fps));
  for (let i = 0; i < total; i++) {
    await win.webContents.executeJavaScript(`window.__kurguSeek(${(i / fps).toFixed(5)})`);
    // Seek, sayfada iki animasyon karesi bekler; ardından güncel yüzeyi doğrudan yakala.
    // ('paint' olayı bazen önceki kareye ait geliyordu ve aynı kare iki kez yazılıyordu.)
    let img = await win.webContents.capturePage();
    if (!img || img.isEmpty()) img = await nextPaint();
    if (!img) throw new Error("Kare yakalanamadı");
    const size = img.getSize();
    const out = size.width === width && size.height === height ? img : img.resize({ width, height, quality: "best" });
    fs.writeFileSync(path.join(outDir, `${String(i).padStart(5, "0")}.png`), out.toPNG());
  }
  win.destroy();
  fs.writeFileSync(path.join(outDir, "done.json"), JSON.stringify({ frames: total }));
}

module.exports = { run };
