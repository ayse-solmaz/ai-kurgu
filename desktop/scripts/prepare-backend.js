// Kurulum paketine girecek Python motoru dosyalarını desktop/build-backend altına toplar.
// Python ortamı pakete gömülmez; kullanıcının bilgisayarında ilk açılışta uv ile kurulur
// (paket küçük kalır, OpenVINO/Whisper gibi kütüphaneler doğru platform için iner).
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const desktop = path.resolve(__dirname, "..");
const root = path.resolve(desktop, "..");
const out = path.join(desktop, "build-backend");

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });

const copy = (rel, dstRel = rel) => {
  const src = path.join(root, rel);
  if (!fs.existsSync(src)) throw new Error(`Eksik: ${rel}`);
  fs.cpSync(src, path.join(out, dstRel), {
    recursive: true,
    filter: (p) => !/__pycache__|\.pyc$/.test(p),
  });
};

for (const item of [
  "src", "pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSES.md",
  "config.yaml", "models_registry.yaml", "agents.yaml", "presets", "assets", "motion", "scripts",
]) copy(item);

// Derlenmiş arayüz
const dist = path.join(root, "web", "dist");
if (!fs.existsSync(path.join(dist, "index.html"))) {
  console.log("web/dist yok, arayüz derleniyor...");
  execSync("npm run build", { cwd: path.join(root, "web"), stdio: "inherit" });
}
copy(path.join("web", "dist"));

// uv (MIT/Apache-2.0): ilk açılışta Python ve kütüphaneleri kurmak için
const uvPath = execSync(process.platform === "win32" ? "where uv" : "which uv").toString().split(/\r?\n/)[0].trim();
fs.mkdirSync(path.join(out, "bin"), { recursive: true });
fs.copyFileSync(uvPath, path.join(out, "bin", process.platform === "win32" ? "uv.exe" : "uv"));

const size = (dir) => fs.readdirSync(dir, { withFileTypes: true })
  .reduce((s, e) => s + (e.isDirectory() ? size(path.join(dir, e.name)) : fs.statSync(path.join(dir, e.name)).size), 0);
console.log(`Hazır: ${out} (${(size(out) / 1e6).toFixed(1)} MB)`);
