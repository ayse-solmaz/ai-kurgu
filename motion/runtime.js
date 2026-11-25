/*
 * AI Kurgu hareketli grafik çalışma zamanı.
 *
 * Şablonlar normal HTML/CSS/JS'tir. Render motoru (Electron, görünmez pencere) her kare için
 * window.__kurguSeek(t) çağırır: tüm CSS/Web animasyonları t anına sarılır, K.onFrame ile kaydedilen
 * fonksiyonlar (sayaç, çizim vb.) çalışır ve kare şeffaf arka planla yakalanır. Böylece çıktı
 * gerçek zamandan bağımsız, kare kare tutarlıdır.
 *
 * Şablon içinde kullanılabilenler:
 *   K.params        template.json'daki parametreler (LLM veya kullanıcı doldurur)
 *   K.duration      saniye cinsinden süre
 *   K.el(tag, cls, html)    eleman oluşturur
 *   K.at(el, saniye)        elemanın animasyon gecikmesini ayarlar (CSS'te var(--d))
 *   K.onFrame(fn)           her karede fn(t) çağrılır (sayaç, ilerleme çubuğu gibi JS animasyonları)
 *   K.ease(x)               0..1 yumuşak geçiş (easeOutCubic)
 *   K.esc(metin)            HTML kaçışı
 */
(function () {
  const P = window.KURGU_PARAMS || {};
  const D = Number(window.KURGU_DURATION || 3.5);
  const hooks = [];

  window.K = {
    params: P,
    duration: D,
    onFrame(fn) { hooks.push(fn); },
    el(tag, cls, html) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (html != null) e.innerHTML = html;
      return e;
    },
    at(e, s) { e.style.setProperty("--d", `${s}s`); return e; },
    ease(x) { x = Math.min(Math.max(x, 0), 1); return 1 - Math.pow(1 - x, 3); },
    esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); },
    icon(svg, cls = "icon") { const w = K.el("div", cls); w.innerHTML = svg || ""; return w; },
  };

  const root = document.documentElement;
  // CSS büyük harf dönüşümü dile göre çalışır: Türkçede i -> İ, ı -> I
  root.lang = window.KURGU_LANG || P.lang || "tr";
  // görsel tema (base.css [data-theme]); render motoru KURGU_THEME'i enjekte eder
  root.dataset.theme = window.KURGU_THEME || new URLSearchParams(location.search).get("theme") || "apple";
  root.style.setProperty("--dur", `${D}s`);
  root.style.setProperty("--out", `${Math.max(D - 0.45, 0.2)}s`);
  if (P.accent) root.style.setProperty("--accent", P.accent);

  window.__kurguSeek = function (t) {
    for (const a of document.getAnimations()) {
      try { a.pause(); a.currentTime = t * 1000; } catch (e) { /* yoksay */ }
    }
    for (const fn of hooks) { try { fn(t); } catch (e) { console.error(e); } }
    return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true))));
  };

  window.__kurguReady = new Promise((resolve) => {
    const done = () => (document.fonts ? document.fonts.ready.then(resolve) : resolve());
    if (document.readyState === "complete") done();
    else window.addEventListener("load", done);
  });

  // Tarayıcıda doğrudan açılınca (önizleme/geliştirme) gerçek zamanlı oynat
  if (!window.KURGU_RENDER) {
    window.addEventListener("load", () => {
      const t0 = performance.now();
      const loop = () => {
        const t = ((performance.now() - t0) / 1000) % (D + 0.6);
        hooks.forEach((fn) => fn(t));
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    });
  }
})();
