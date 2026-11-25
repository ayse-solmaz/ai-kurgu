# Hareketli grafik şablon kütüphanesi

Her şablon `motion/templates/<şablon-id>/` altında iki dosyadan oluşur. Klasörü eklediğin an uygulama
şablonu tanır; **animasyon tasarımcısı ajanı da onu kullanmaya başlar** (açıklamasını okuyup ne zaman
uygun olduğuna kendisi karar verir). Arayüzde **Animasyon → Şablonlar** galerisinden önizleyebilirsin.

```
motion/
  base.css          ortak tasarım sistemi (fontlar, cam kart, giriş animasyonları)
  runtime.js        kare kare render çalışma zamanı (K nesnesi)
  fonts/            Montserrat, Inter (OFL)
  templates/
    big_number/
      template.json
      index.html
```

## template.json

```json
{
  "id": "big_number",
  "name": "Büyük sayı",
  "description": "LLM'e NE ZAMAN kullanılacağını anlatan cümle. En önemli alan budur.",
  "layout": "card",            // card: konuşmacının üstünde şeffaf kart | full: tam ekran anlatım
  "hide_captions": false,      // true: bu animasyon ekrandayken altyazı gizlenir
  "duration": { "default": 3.4, "min": 2.5, "max": 4.5 },
  "params": {
    "value":  { "type": "number", "required": true, "description": "..." },
    "label":  { "type": "string", "required": true, "max": 40 },
    "icon":   { "type": "icon", "description": "İngilizce anahtar kelime: 'fire', 'brain'" },
    "position": { "type": "enum", "values": ["top", "center"], "default": "top" },
    "steps":  { "type": "list", "min": 2, "max": 4,
                "item": { "icon": { "type": "icon" }, "label": { "type": "string", "max": 30 } } },
    "words":  { "type": "list", "max": 4, "item_type": "string", "item_max": 20 }
  },
  "example": { "value": 10, "label": "daha fazla enerji", "icon": "high voltage" }
}
```

Parametre tipleri: `string` (`max` uzunluk), `number`, `enum` (`values`), `icon`, `list`
(nesne listesi için `item`, metin listesi için `item_type`). Doğrulayıcı tipleri dönüştürür, uzun metinleri
kelime sınırında kısaltır, eksik zorunlu alanı olan öneriyi eler.

**İkonlar:** `icon` tipindeki alana bir anahtar kelime ("thermometer") ya da doğrudan `set:ad`
("fluent-emoji-flat:fire") yazılır. Uygulama Iconify API'den yalnızca ticari kullanıma izin veren setlerde
arar (Fluent Emoji MIT, Noto Apache-2.0, Tabler MIT, Lucide ISC, Phosphor MIT, MDI Apache-2.0), SVG'yi
`assets/icons/` altına önbelleğe alır ve şablona `<alan>_svg` olarak verir (ör. `P.icon_svg`).

## index.html

Normal bir HTML sayfası. `../../base.css` ve `../../runtime.js` dosyalarını bağla, içeriği `#stage` içine kur:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="../../base.css">
<style>
  .card { width: 88vw; padding: 5vw; }
  .val  { font: 800 18vw "Montserrat"; color: var(--accent); }
</style>
</head>
<body>
<div id="stage"></div>
<script src="../../runtime.js"></script>
<script>
  const P = K.params;
  const slot = K.el("div", `card-slot pos-${P.position || "top"}`);
  const card = K.at(K.el("div", "glass card a-rise"), 0);        // 0. saniyede yükselerek gelir
  card.appendChild(K.icon(P.icon_svg));                            // indirilen ikon
  const val = K.el("div", "val"); card.appendChild(val);
  card.appendChild(K.at(K.el("div", "a-fade", K.esc(P.label)), 0.5));  // 0.5 sn sonra belirir
  slot.appendChild(card);
  document.getElementById("stage").appendChild(slot);
  K.onFrame((t) => { val.textContent = Math.round(P.value * K.ease((t - 0.2) / 1.2)); });  // sayaç
</script>
</body>
</html>
```

### Kurallar

- **Animasyonlar CSS (`@keyframes`) ya da `K.onFrame(t => ...)` ile yapılır.** Render motoru her kare için
  zamanı ileri sarar; `setTimeout`, `setInterval` ve gerçek saat (`Date.now`) KULLANMA, yoksa kareler tutarsız olur.
- Gecikme için `K.at(eleman, saniye)` kullan; base.css'teki giriş sınıfları bunu okur:
  `a-pop`, `a-rise`, `a-fade`, `a-slide-l`, `a-slide-r`, `a-grow-x`, `a-float`, `a-pulse`.
- Çıkış otomatiktir: `#stage` son 0.45 saniyede kaybolur.
- Ölçüleri `vw` ile ver (1080 px genişlikte 1vw = 10.8 px); şablon her çözünürlükte aynı görünür.
- Arka plan şeffaftır (kart şablonları videonun üstüne biner). Tam ekran şablonlarda `<div class="fullbg">` kullan.
- Altyazı ekranın %70-78 yüksekliğindedir; kartları `pos-top` / `pos-center` bölgesinde tut.
- Metinleri `K.esc()` ile ekle (LLM'den gelen metin HTML olarak yorumlanmasın).
- Yardımcılar: `K.el`, `K.at`, `K.icon`, `K.ease`, `K.esc`, `K.onFrame`, `K.params`, `K.duration`.
- Renkler: `--accent` (kehribar), `--accent-2`, `--cool` (camgöbeği), `--violet`, `--danger`, `--ok`.
- Sayfa dili Türkçe ayarlıdır; CSS `text-transform: uppercase` "i" harfini doğru şekilde "İ" yapar.

### Deneme

```bash
uv run kurgu motion list                         # şablonları listele
uv run kurgu motion preview big_number           # örnek parametrelerle render -> motion_preview.mp4
uv run kurgu animate <proje>                     # ajan şablonları videoya yerleştirir
```
