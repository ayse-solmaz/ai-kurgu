# AI Kurgu

Konuşmalı videoları (talking-head, anlatım, eğitim videoları) **açık kaynak modellerle, kendi bilgisayarında**
otomatik kurgulayan, ajan tabanlı bir video kurgu aracı.

Videoyu bırakırsın, hiçbir şey yazmazsın:

1. Analiz araçları sesi metne çevirir, sessizlikleri, "ııı/eee" dolgularını ve tekrar çekimleri bulur, videonun nasıl bir video olduğunu betimler.
2. Yerel bir LLM (senarist) temizlenmiş metnin tamamını okuyup bir kurgu senaryosu yazar: anlatı yapısı, açılış cümlesi, hangi anda konuşmacı / b-roll / animasyon / yazı gösterileceği.
3. Açık kaynak modellerden oluşan ajanlar bu senaryoyu uygular: kesim, altyazı, üretilmiş b-roll görselleri, hareketli grafikler, fon müziği.
4. Tek bir ffmpeg geçişiyle final video çıkar. İstersen sonra sohbetle ("ilk 3 saniyeyi kes", "0:20'ye uzay görseli koy") düzenlersin.

Hiçbir bulut LLM'i veya ücretli API kullanılmaz. Her şey yerelde, açık ağırlıklı modellerle çalışır.

> **Durum: erken aşama, kişisel proje.** Kendi videolarımı kurgulamak için geliştiriyorum. Çalışıyor ama
> kalite tutarlı değil; aşağıdaki [Dürüst durum](#dürüst-durum) bölümüne bak.

---

## Nasıl çalışıyor?

```
video ─► ingest (ffprobe, proxy, 16 kHz ses)
      ─► analiz: faster-whisper (kelime zaman damgaları) · Silero VAD · dolgu/kekeleme/tekrar çekim · yüz takibi · Qwen3-VL sahne tarifi
      ─► temizlik: sessizlik + dolgu + tekrar çekim kesimi (kural tabanlı, LLM ile doğrulama)
      ─► senarist (Qwen3 30B-A3B): brif + sahne planı (metnin tamamını görerek)
      ─► uygulayıcı ajanlar: b-roll sanat yönetmeni · animasyon tasarımcısı · yazı · greenscreen/CTA · besteci
      ─► kod tabanlı kalite kapıları (sayı doğrulama, anlamsız kart eleme, dağılım dengesi, yüz bölgesinden kaçınma)
      ─► render: tek ffmpeg grafiği, ASS altyazı, Intel QSV
```

Tek gerçek kaynak bir **Timeline JSON**'dur. Altyazı, b-roll ve animasyonlar saniyeye değil transkriptteki
**kelime kimliklerine** bağlanır; kesim yapılınca hiçbir şey kaymaz. Transkriptten kelime silmek = videodan kesmek.
Her değişiklik yeni bir sürümdür (geri al / yinele).

Küçük yerel modeller yaratıcı işte zayıf olduğu için kararların önemli kısmı **kodla denetlenir**: ekranda
gösterilen bir sayı konuşmada geçmiyorsa kart reddedilir, cümle parçasından oluşan madde listeleri elenir,
"önce/sonra" gibi sıra sözcükleri yoksa süreç animasyonu konmaz, kartlar konuşmacının yüzünün olmadığı
bölgeye yerleştirilir.

## Özellikler

| Alan | Ne yapıyor |
|---|---|
| Transkript | faster-whisper large-v3-turbo, kelime kelime zaman damgası |
| Temizlik | Sessizlik (Silero VAD), "ııı/eee/hmm" dolguları, "ben ben" kekelemeleri, "bir daha / pardon / baştan" ile başlayan tekrar çekimler |
| Shorts | 9:16, en fazla 60 sn. Uzun videodan hikâyeyi kuran cümleler seçilir (açılış, sorun, çözüm, sonuç; ardışık olmak zorunda değil); önceki cümleye bağlı veya yarım kalan cümleler kodla tamamlanır ya da çıkarılır. Yüz takipli dikey kadraj (OpenCV YuNet) |
| Senaryo | Video profili (tempo, enerji, tür, ton) + senarist ajanı; sahne başına karar, kodla dengelenen dağılım |
| B-roll | Yerel FLUX.1-schnell (OpenVINO INT4) + Ken Burns hareketi; isteğe bağlı bulut GPU worker veya Pexels/Pixabay yedeği |
| Hareketli grafik | HTML/CSS şablonları (süreç adımları, zaman çizelgesi, büyük sayı, termometre, madde listesi, uyarı kartı, kelime vurgusu, ikon) Electron'un Chromium'uyla şeffaf video olarak render edilir. Kendi şablonunu eklemek için: [motion/README.md](motion/README.md) |
| İkonlar | Iconify API'den tek renk çizgi ikonlar (Phosphor, Lucide, Tabler, MDI); marka logoları filtrelenir |
| Temalar | Sade (Inter, sistem renkleri, buzlu cam kartlar; varsayılan), bold, neon, minimal |
| Müzik | ACE-Step 1.5 ile videoya özel enstrümantal fon müziği; konuşmada otomatik kısılır (ducking) |
| Ses | Gürültü azaltma, -14 LUFS, isteğe bağlı Türkçe dış ses (Chatterbox) |
| Sohbetle düzenleme | Ajan, timeline üzerinde araç çağrılarıyla değişiklik yapar ve yeni sürüm oluşturur |
| Arayüz | Web (FastAPI + React, [Astryx](https://github.com/meta/astryx) tasarım sistemi) ve Electron masaüstü uygulaması |

## Dürüst durum

Neyin gerçekten denendiğini ve neyin zayıf olduğunu açıkça yazıyorum:

**Düzenli denenen yol:** Windows 11, Intel Core Ultra 9 285H (Arc iGPU, NVIDIA yok), 31 GB RAM. Türkçe, tek
konuşmacılı, 3-5 dakikalık iki video üzerinde tam otomatik kurgu (Shorts ve dikey tam video).

**Zayıf noktalar:**

- **Kalite tutarlı değil.** Sonuç büyük ölçüde yerel LLM'e bağlı. 30B modelle 58 saniyelik bir Shorts'ta
  3 b-roll ve 3 animasyon çıktı; temiz ama sıradan. Kalite kapısından geçemeyen öneriler artık atılmak yerine
  sade ikon kartlarına çevriliyor, yine de ikon seçimi bazen konuyla zayıf eşleşiyor. 8B modelde animasyon ve b-roll'ların önemli kısmı kalite
  kapılarından elenip video boş kalıyordu.
- **Yavaş.** 30B model ekran kartsız bilgisayarda işlemcide çalışıyor (~12 token/sn). 5 dakikalık bir videonun
  tam otomatik kurgusu ~15-25 dakika sürüyor. Masaüstü uygulaması iş sürerken bilgisayarın uykuya
  geçmesini engeller (uyku, işi saatlerce durdurabiliyordu).
- **Bellek.** Qwen3 30B-A3B ~18.6 GB. 16 GB RAM'li bilgisayarda çalışmaz; uygulama o zaman qwen3:8b'ye düşer.
- **Shorts seçimi LLM'e bağlı:** cümleler arası anlam bağı ("bu sorun" gibi önceki bir cümleye gönderme) her zaman yakalanmıyor.
- **Tekrar çekim tespiti kural tabanlı** ve Türkçe konuşma kalıplarına göre ayarlandı. Başka dillerde veya
  farklı konuşma tarzlarında kaçırabilir ya da fazla kesebilir.
- **Müzik üretimi yavaş:** Intel iGPU'da (XPU) PyTorch sürücü hatası nedeniyle ACE-Step işlemcide çalışıyor,
  60 saniyelik parça ~4 dakika.
- **Az denenen kısımlar:** bulut GPU worker, yerel metinden videoya (Wan), Chatterbox dış ses, stok video
  yedeği. Kodları var ama düzenli test edilmedi.
- **Yalnızca Windows'ta denendi.** Linux/macOS ve NVIDIA/CUDA yolu kod olarak var ama test edilmedi.
- **Arayüz kütüphanesi (Astryx) beta sürümde**; arayüz bu sürümde yeniden yazıldı, kenar durumlar hatalı olabilir.

## Kurulum (Windows)

Gereksinimler: Windows 10/11, ~40 GB boş disk (modeller dahil), önerilen 32 GB RAM.

```powershell
git clone https://github.com/ayse-solmaz/ai-kurgu.git
cd ai-kurgu
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Betik şunları kurar: uv, ffmpeg, Python 3.12 ortamı, Ollama ve modeller (`qwen3:30b-a3b`, yedek `qwen3:8b`,
`qwen3-vl:4b`), açık lisanslı yazı tipleri ve web arayüzü. İsteğe bağlı:

- `-WithImageModel`: yerel b-roll görsel üretimi için FLUX.1-schnell INT4 (~9.3 GB)
- `-WithTTS`: Chatterbox dış ses (~2 GB)

Müzik üretimi ayrı kurulur (~6 GB, kendi Python ortamında):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_music.ps1            # işlemci
powershell -ExecutionPolicy Bypass -File scripts\setup_music.ps1 -Device cuda
```

> Uygulama, kendi Ollama sunucusunu ayrı bir portta (11435) başlatır; mevcut Ollama ayarlarına dokunmaz,
> aynı model klasörünü kullanır.

## Kullanım

### Web arayüzü

```powershell
uv run kurgu serve
```

http://127.0.0.1:8765 adresini aç, videoyu sürükle, formatı seç (Shorts / Dikey / YouTube), "Kurguya başla".

Arayüz geliştirme: `cd web; npm run dev` (http://localhost:5173, API'yi 8765'e yönlendirir).

### Masaüstü uygulaması

```powershell
cd desktop; npm install; npm start        # geliştirme modu
cd desktop; npm run dist                  # kurulum dosyası: desktop/dist/AI Kurgu Setup x.y.z.exe
```

Kurulu sürüm ilk açılışta Python ortamını `uv` ile `%APPDATA%\AI Kurgu` altına kurar. Kurulum dosyası
derlemeden önce `uv run python scripts/fetch_fonts.py` çalıştırılmış olmalı.

### Komut satırı

```powershell
uv run kurgu doctor                                    # kurulum kontrolü
uv run kurgu edit video.mp4 -p shorts --auto           # promptsuz tam otomatik kurgu
uv run kurgu edit video.mp4 -p youtube --auto -i "eğitici ve sakin olsun"
uv run kurgu chat <proje> "ilk 3 saniyeyi kes ve 0:20'ye galaksi görseli koy"
uv run kurgu render <proje> --preview
uv run kurgu transcript <proje>                        # kesilen kelimeler üstü çizili
uv run kurgu undo <proje>
```

### Ayarlar

- `config.yaml`: modeller, cihazlar, b-roll sağlayıcı sırası, render ayarları
- `agents.yaml`: ajanların sistem prompt'ları ve varsayılan stil (arayüzde Senaryo → Gelişmiş'ten de düzenlenir)
- `presets/`: shorts, dikey, youtube
- `.env.example` → `.env`: isteğe bağlı Pexels/Pixabay anahtarları, bulut worker anahtarı

## Kullanılan modeller

| Model | Görev | Lisans |
|---|---|---|
| Qwen3 30B-A3B (yedek: Qwen3 8B) | Senarist, planlayıcı, sohbet ajanı | Apache-2.0 |
| Qwen3-VL 4B | Sahne betimleme | Apache-2.0 |
| Whisper large-v3-turbo (faster-whisper) | Konuşma tanıma | MIT |
| Silero VAD | Sessizlik tespiti | MIT |
| OpenCV YuNet | Yüz takibi | Apache-2.0 / MIT |
| FLUX.1-schnell | B-roll görselleri | Apache-2.0 |
| ACE-Step 1.5 | Fon müziği | MIT |
| Chatterbox Multilingual | Dış ses | MIT |

Tüm bileşenlerin listesi: [LICENSES.md](LICENSES.md).

## Geliştirilecekler

Öncelik sırasına yakın:

- [x] **Kesimleri gözden geçirme:** Transkript → Kesimler listesi; her tekrar çekim/dolgu kesimi nedeniyle gösterilir, tek tıkla geri alınır.
- [x] **Doğru anda animasyon:** kartlar yazdıkları kelimelerin, b-roll'lar etiketlendikleri ifadenin konuşmada GERÇEKTEN söylendiği ana bağlanır.
- [x] **Shorts hikâye seçimi:** tek ardışık bölüm yerine hikâyeyi kuran cümleler; video hazırlık cümlesinde bitmez.
- [ ] **Kesimleri uygulamadan önce onaylatmak:** şu an kesimler önce uygulanıyor, sonra geri alınabiliyor; isteğe bağlı "önce sor" modu.
- [ ] **Senaryo kalitesi:** kullanıcının arayüzde yaptığı düzeltmeleri (silinen b-roll, geri alınan kesim, değiştirilen yazı) kaydedip küçük bir modeli (Qwen3 4B/8B) bu dar işler için LoRA ile ince ayarlamak.
- [x] **İçeriğe göre animasyon sayısı:** sayı, süreç, uyarı, karşılaştırma içeren cümlelere göre hedef belirlenir.
- [ ] **Şablon kütüphanesini genişletmek** (karşılaştırma, harita, alıntı kartı vb.).
- [ ] **Hız:** senarist çağrılarını azaltmak, analiz sonuçlarını önbelleklemek, uzun videolar için paralel işleme.
- [x] **Yüzden kaçan yerleşim:** kartlar, açılış yazısı (hook) ve ekran yazıları konuşmacının yüzünün olmadığı bölgeye yerleşir.
- [ ] **Diğer diller:** dolgu ve tekrar çekim kuralları şu an Türkçe odaklı.
- [ ] **Platformlar:** Linux ve macOS desteği, NVIDIA/CUDA ve Apple Silicon yolunun test edilmesi.
- [ ] **Müzik:** Intel iGPU'da ACE-Step (sürücü hatası çözülünce), daha kısa üretim süresi.
- [ ] **Zaman çizelgesi:** sürükle-bırak ile b-roll/animasyon taşıma ve süre değiştirme.
- [x] **CI:** birim testleri ve arayüz derlemesi her push'ta GitHub Actions'ta çalışır.
- [ ] **Uçtan uca test:** örnek bir videoyla tam otomatik kurgunun CI'da (küçük modelle) denenmesi.

## Proje yapısı

```
src/kurgu/
  core/       timeline (tek gerçek kaynak), zaman eşleme, proje deposu, ffmpeg, Ollama sunucusu
  ingest/     ffprobe, analiz sesi, proxy
  analyze/    ASR, VAD, dolgu/kekeleme/tekrar çekim, yüz takibi, video profili
  agent/      LLM istemcisi, yönetmen, senarist, animasyon tasarımcısı, sohbet ajanı, denetçi
  generate/   b-roll, hareketli grafik, ikonlar, müzik, greenscreen, ses efektleri, dış ses
  render/     ASS altyazı, temalar, kadraj, ffmpeg render
  api/        FastAPI + iş kuyruğu
  cli.py      komut satırı
motion/       hareketli grafik şablonları (HTML/CSS)
web/          React arayüzü (Astryx)
desktop/      Electron masaüstü uygulaması
worker_cloud/ isteğe bağlı GPU worker (FastAPI + diffusers)
presets/      shorts, dikey, youtube
```

Testler: `uv run --group dev pytest`

## Lisans

Projenin kendi kodu [MIT](LICENSE) lisanslıdır. Kullanılan modeller ve kütüphaneler kendi lisanslarına
tabidir: [LICENSES.md](LICENSES.md).
