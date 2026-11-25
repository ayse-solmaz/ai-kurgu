# Bileşenler ve Lisanslar

Projede kullanılan modeller, kütüphaneler ve varlıklar. Varsayılan zincir yalnızca açık lisanslı
bileşenlerden oluşur. `models_registry.yaml` her modelin lisansını tutar; `config.yaml` içindeki
`commercial_mode: true` ayarı, kullanım kısıtı olan modellerin (aşağıda "kısıtlı" tablosu) yüklenmesini
engeller. Bu tablo hukuki görüş değildir; her lisansı kendin okuyup kontrol et.

## Varsayılan bileşenler

| Bileşen | Kullanım | Lisans |
|---|---|---|
| faster-whisper + Whisper large-v3-turbo | Transkript | MIT |
| Silero VAD (faster-whisper içinde) | Sessizlik tespiti | MIT |
| Qwen3 30B-A3B / Qwen3 8B (Ollama) | Senarist, planlayıcı, sohbet ajanı | Apache-2.0 |
| Qwen3-VL 4B (Ollama) | Sahne betimleme | Apache-2.0 |
| OpenCV + YuNet | Yüz takibi | Apache-2.0 / MIT |
| OpenVINO, OpenVINO GenAI | Intel CPU/iGPU çıkarımı | Apache-2.0 |
| FLUX.1-schnell | Görsel üretimi | Apache-2.0 |
| Wan 2.1 / 2.2 | Video üretimi (isteğe bağlı) | Apache-2.0 |
| ACE-Step 1.5 (turbo DiT + VAE + Qwen3-Embedding-0.6B) | Müzik üretimi | MIT (Qwen3-Embedding: Apache-2.0) |
| Chatterbox Multilingual | Dış ses (isteğe bağlı) | MIT |
| FastAPI, Uvicorn, Pydantic, Typer, Rich | Backend / CLI | MIT / BSD |
| React, Vite, Tailwind CSS | Arayüz | MIT |
| Astryx (Meta) | Arayüz bileşenleri ve tema | MIT |
| Heroicons | Arayüz ikonları | MIT |
| Electron (Chromium) | Masaüstü uygulaması + hareketli grafik render'ı | MIT |
| Inter, Inter Tight, Montserrat (fontsource) | Altyazı ve animasyon yazı tipleri (`scripts/fetch_fonts.py` indirir) | SIL OFL 1.1 |
| Phosphor / Lucide / Tabler / MDI | Animasyon ikonları (Iconify API, marka logoları hariç) | MIT / ISC / MIT / Apache-2.0 |
| Fluent Emoji (Microsoft), Noto Emoji (Google) | Renkli temalardaki ikonlar (Iconify API) | MIT / Apache-2.0 |
| Hareketli grafik şablonları (`motion/`), CTA ikonları | Bu projede yazıldı | MIT |
| Ses efektleri | ffmpeg ile sentezlenir | - |
| ffmpeg | Render | Kullanılan derlemeye göre LGPL/GPL |

"Sade" (apple) tema Apple'ın tasarım dilinden esinlenir; SF Pro, SF Symbols veya Apple'a ait başka
varlık **kullanılmaz** (lisansları buna izin vermez). Yerine Inter ve özgün çizimler kullanılır.

## İsteğe bağlı / kısıtlı

| Bileşen | Not |
|---|---|
| MiniMax-H3-Image | Yalnızca bulut worker'da, varsayılan kapalı. Lisansı bazı bölgeleri kapsamaz ve kullanım koşulları içerir |
| LTX-Video | Varsayılan kapalı; özel açık ağırlık lisansı |
| SDXL base (OpenVINO INT8) | CreativeML OpenRAIL++-M kullanım kısıtlamaları |
| Piper TTS 1.3+ | GPL-3 |
| Pexels / Pixabay | Ücretsiz stok video yedeği; açık kaynak değil, atıf ve API limitleri var |
| Kaggle / Colab ücretsiz GPU | Bulut worker'ı kişisel olarak denemek için |

## Kullanıcı içeriği

- Ses klonlama yalnızca sesin sahibinin onayıyla yapılır (`KURGU_VOICE_CONSENT=1`).
- Stok içerik atıfları timeline'da saklanır ve arayüzde "Bilgi" sekmesinde gösterilir.
