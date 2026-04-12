# 🤖 ROBOT KANKA — TAM TEKNİK YOL HARİTASI
### Raspberry Pi 5 | PiCar-X | Türkçe Sesli AI Asistan
**Hazırlayan:** Claude AI Analizi
**Tarih:** Nisan 2026
**Donanım:** Raspberry Pi 5 - 8GB + Aktif Soğutucu + PiCar-X Gövde

---

## 📋 İÇİNDEKİLER

1. Donanım Özeti
2. Mevcut Sorunun Kök Nedeni
3. Hedef Mimari
4. Teknoloji Kararları
5. Aşama Aşama Yol Haritası (8 Modül)
6. Proje Dosya Yapısı
7. Sistem Promptu & Persona
8. Kabul / Teslim Testleri
9. Maliyet ve API Kontrol Notları
10. Geliştirici Kontrol Listesi

---

## 1. DONANIM ÖZETİ

| Bileşen | Model / Detay |
|---|---|
| Ana kart | Raspberry Pi 5 — 8GB RAM |
| Soğutma | Aktif fanlı soğutucu (resmi) |
| Güç | 27W USB-C orijinal adaptör |
| Gövde | PiCar-X (tekerlekli robot şasi) |
| Kamera | Çift kamera modülü ("göz" tasarımı, CSI ribbon) |
| Sensör | Ultrasonik mesafe sensörü (ön) |
| Ses çıkışı | Robot-HAT I2S hoparlör |
| Ses girişi | USB mikrofon |
| HAT | Robot-HAT (SunFounder PiCar-X HAT) |

---

## 2. MEVCUT SORUNUN KÖK NEDENİ

Önceki mühendis tarafından yapılan kurulumda mimari hatalar var. Sorun donanımda değil, **servis yönetiminde**.

**Tespit edilen hatalar:**
- Tüm servisler (mikrofon, hoparlör, kamera, internet, OpenAI) **aynı anda** başlatılmaya çalışılıyor
- Raspberry Pi açılışında kaynaklar hazır olmadan yüklenmeye başlıyor
- `network-online.target` ve `sound.target` beklenmeden servis ayağa kalkıyor
- Gecikmeli başlatma (delayed startup) uygulanmamış
- Hazırlık kontrolleri (internet, ses, kamera) eksik
- Loglama yok → ekransız debug edilemiyor
- Çökme durumunda `Restart=always` eksik

**Doğru yaklaşım:** Her bileşen için ayrı systemd servisi, sıralı ve kontrollü başlatma.

---

## 3. HEDEF MİMARİ

```
[Mikrofon - sürekli açık]
        ↓
[VAD - LOCAL]  ←  bedava, Pi'de çalışır, sessizliği keser
        ↓ (konuşma algılandı)
[Wake Word Kontrol]  ←  "Kanka" / "Cihan" / "Hey robot"
        ↓ (wake word geçti)
[LOCAL STT - Whisper]  ←  sadece bu noktada devreye girer
        ↓
[OpenAI Chat API]  ←  sadece TEXT gönderilir, ses gitmez (fatura kontrolü)
        ↓
[TTS - Karar Noktası]
   ├── İnternet VAR  → OpenAI TTS (Spruce sesi) — doğal, sıcak
   └── İnternet YOK → Piper TTS (tr_TR-ahmet-medium) — offline yedek
        ↓
[Hoparlör - cevap oynatılır]
```

**Bu mimarinin avantajları:**
- OpenAI'ye sadece TEXT gider → fatura minimum
- VAD her zaman açık ama işlem yapmaz → Pi ısınmaz
- Internet kesilirse sistem çalışmaya devam eder
- Her adım loglanabilir, test edilebilir

---

## 4. TEKNOLOJİ KARARLARI

### STT (Konuşma → Metin)
| Seçenek | Pi'de Hız | Türkçe Kalitesi | Maliyet |
|---|---|---|---|
| **Whisper.cpp (small model)** | ~2-3 sn | Çok iyi | Bedava |
| Whisper Python (tiny) | ~1-2 sn | İyi | Bedava |
| Google Cloud STT | ~0.5 sn | Mükemmel | Ücretli |

**Öneri: `whisper.cpp` + `small` model** — Pi 5'in 8GB'ı bunu rahat kaldırır.

### TTS (Metin → Ses)
| Seçenek | İnternet | Kalite | Kullanım |
|---|---|---|---|
| **OpenAI TTS - Spruce** | Gerekli | Mükemmel (insan gibi) | Ana ses |
| **Piper - tr_TR-ahmet-medium** | Gerekmez | İyi (robotik ama anlaşılır) | Offline yedek |

### Wake Word
**`Porcupine` (Picovoice)** — ARM için optimize, Pi'de hızlı, özel kelime eğitilebilir.
Alternatif: `OpenWakeWord` (tamamen ücretsiz).

### VAD (Sessizlik Tespiti)
**`silero-vad`** — PyTorch tabanlı, hızlı, Türkçe'de de çalışır.

### LLM
**OpenAI GPT-4o-mini** — En düşük gecikme + maliyet dengesi.

---

## 5. AŞAMA AŞAMA YOL HARİTASI

---

### MODÜL A — OS TEMELI & KURULUM
**Süre:** 2-4 saat | **Önkoşul:** Yeni SD kart veya temiz format

**Yapılacaklar:**
1. Raspberry Pi OS 64-bit Lite (ekransız) kurulumu — `rpi-imager` ile
2. SSH aktif, Wi-Fi otomatik bağlanma (`/etc/wpa_supplicant/`)
3. Statik IP veya hostname ile erişim (`robot.local`)
4. Sistem güncellemeleri: `sudo apt update && sudo apt upgrade -y`
5. Temel paketler: `git python3-pip python3-venv alsa-utils pulseaudio`
6. Robot-HAT sürücüsü kurulumu (SunFounder resmi repo)
7. Proje dizini ve Python virtual environment:
   ```
   /home/robot/kanka/
   python3 -m venv venv
   pip install -r requirements.txt
   ```
8. `install.sh` scripti — tek komutla her şeyi kurar

**Teslim Kriteri:** SSH ile bağlanılıyor, `hostname` → `robot` döner, Pi kararlı.

---

### MODÜL B — SES ÇIKIŞI (HOPARLÖR)
**Süre:** 2-3 saat | **Kritik:** Reboot sonrası bozulmamalı

**Yapılacaklar:**
1. Robot-HAT I2S ses sürücüsü overlay kontrolü (`/boot/config.txt`)
2. `~/.asoundrc` ile varsayılan ses cihazı kalıcı ayarı
3. PulseAudio veya ALSA yapılandırması (hangisi Pi 5 ile daha stabil)
4. Test: `speaker-test -t wav -c 2`
5. Piper TTS kurulumu:
   ```bash
   pip install piper-tts
   # Model indir:
   piper --download tr_TR-ahmet-medium
   ```
6. Açılış anonsu scripti:
   ```python
   # startup_announce.py
   tts.speak("Sistem hazır. Merhaba Cihan.")
   ```
7. `speaker_test.sh` — reboot sonrası çalıştırılacak test scripti

**Teslim Kriteri:** `sudo reboot` sonrası hoparlörden ses geliyor.

---

### MODÜL C — MİKROFON (USB)
**Süre:** 1-2 saat

**Yapılacaklar:**
1. USB mikrofon cihaz ID tespiti: `arecord -l`
2. Varsayılan input kalıcı ayarı (`~/.asoundrc` güncelleme)
3. Ses seviyesi ayarı: `alsamixer` veya `amixer`
4. Kayıt testi:
   ```bash
   arecord -d 5 -f cd test.wav && aplay test.wav
   ```
5. Gürültü eşiği kalibrasyonu (ortam sessizlik seviyesi ölçümü)
6. `mic_test.sh` — "ben duyuyorum" doğrulama scripti

**Teslim Kriteri:** Kayıt yapılıyor, playback çalışıyor, reboot sonrası hâlâ çalışıyor.

---

### MODÜL D — TÜRKÇE STT (KONUŞMA ALGILAMA)
**Süre:** 3-5 saat | **En kritik kalite noktası**

**Yapılacaklar:**
1. `whisper.cpp` derleme (Pi 5 ARM64 için optimize):
   ```bash
   git clone https://github.com/ggerganov/whisper.cpp
   cd whisper.cpp && make -j4
   bash models/download-ggml-model.sh small
   ```
2. Python wrapper entegrasyonu
3. VAD (Voice Activity Detection) kurulumu:
   ```python
   # silero-vad ile sadece konuşma segmentlerini kes
   pip install silero-vad
   ```
4. Wake word sistemi:
   ```python
   # OpenWakeWord (ücretsiz) veya Porcupine
   # Tetik kelimeler: "Kanka", "Cihan", "Hey robot"
   ```
5. STT pipeline:
   ```python
   mic_input → VAD → wake_word_check → whisper_stt → text_output
   ```
6. Türkçe dil testi: "Kanka nasılsın?" → doğru transkript
7. Log dosyası: `heard: "kanka nasılsın" | confidence: 0.94`

**Önemli Ayarlar:**
- Whisper language: `tr`
- VAD threshold: 0.5 (gürültülü ortam için biraz düşük)
- Sessizlik sonrası kesme: 1.5 saniye

**Teslim Kriteri:** Türkçe cümle tek seferde anlaşılıyor, log dosyasına yazılıyor.

---

### MODÜL E — DİYALOG SİSTEMİ (LLM + TTS)
**Süre:** 3-4 saat

**Yapılacaklar:**
1. `.env` dosyası:
   ```env
   OPENAI_API_KEY=sk-...
   MODEL=gpt-4o-mini
   TTS_VOICE=spruce
   MAX_TOKENS=200
   TIMEOUT_SECONDS=8
   ```
2. Ana konuşma döngüsü:
   ```python
   while True:
       text = listen_and_transcribe()      # STT
       response = ask_openai(text)         # LLM
       audio = text_to_speech(response)    # TTS (online/offline)
       play_audio(audio)                   # Hoparlör
   ```
3. Hafıza entegrasyonu (her API çağrısında):
   ```python
   system_prompt = read_profile() + "\n\n" + BASE_SYSTEM_PROMPT
   ```
4. TTS karar mantığı:
   ```python
   if internet_available():
       use_openai_tts(text, voice="spruce")
   else:
       use_piper_tts(text, model="tr_TR-ahmet-medium")
   ```
5. Hata yönetimi:
   - API timeout → "Bir saniye kanka, bağlantı yavaş."
   - Rate limit → exponential backoff + retry
   - Genel hata → offline Piper ile "Bir sorun oluştu." yanıtı
6. Konuşma log formatı:
   ```
   [2026-04-11 19:32:11] HEARD: kanka nasılsın
   [2026-04-11 19:32:11] SENT_TO_LLM: kanka nasılsın
   [2026-04-11 19:32:13] RESPONSE: İyiyim kanka, sen nasılsın?
   [2026-04-11 19:32:13] TTS: openai-spruce | duration: 2.1s
   ```

**Gecikme Hedefi:** STT(2s) + API(1.5s) + TTS(0.5s) = **~4 saniye** ✓

**Teslim Kriteri:** 10 dakika kesintisiz konuşma, tüm adımlar loglanıyor.

---

### MODÜL F — KAMERA ("GÖZLER")
**Süre:** 2-3 saat

**Yapılacaklar:**
1. Pi 5 kamera konfigürasyonu (`/boot/config.txt` + `libcamera`)
2. Kamera test:
   ```bash
   libcamera-still -o test.jpg
   ```
3. Komut ile kontrol:
   ```python
   # "gözlerini aç" → kamera aktif
   # "gözlerini kapat" → kamera devre dışı
   # "bak" / "önümde ne var" → fotoğraf çek + OpenAI Vision
   ```
4. Görsel analiz entegrasyonu:
   ```python
   def look_and_describe():
       img = capture_image()
       response = openai.chat.completions.create(
           model="gpt-4o",
           messages=[{"role":"user", "content":[
               {"type":"image_url", "image_url": img_to_base64(img)},
               {"type":"text", "text": "Bu fotoğrafta ne var? Türkçe kısa açıkla."}
           ]}]
       )
       return response
   ```
5. Kamera kilit recovery:
   ```python
   if camera_frozen():
       restart_camera_service()
   ```
6. `camera_test.py` — fotoğraf çek + yorumla test scripti

**Teslim Kriteri:** "Bak" komutuyla fotoğraf çekiliyor ve Türkçe yorumlanıyor.

---

### MODÜL G — EKRANSIZ OTOMATİK ÇALIŞMA (EN KRİTİK)
**Süre:** 2-3 saat

**Systemd Servis Yapısı (doğru sıra önemli):**

```
network-online.target
        ↓
sound.target
        ↓
robot-health-check.service   ← internet + ses kontrolü
        ↓
robot-kanka.service          ← ana uygulama
```

**`/etc/systemd/system/robot-kanka.service`:**
```ini
[Unit]
Description=Robot Kanka AI Service
After=network-online.target sound.target robot-health-check.service
Wants=network-online.target

[Service]
Type=simple
User=robot
WorkingDirectory=/home/robot/kanka
ExecStartPre=/home/robot/kanka/scripts/health_check.sh
ExecStart=/home/robot/kanka/venv/bin/python main.py
Restart=always
RestartSec=10
StartLimitInterval=60
StartLimitBurst=3
StandardOutput=append:/var/log/robot-kanka.log
StandardError=append:/var/log/robot-kanka-error.log

[Install]
WantedBy=multi-user.target
```

**Health Check Scripti (`health_check.sh`):**
```bash
#!/bin/bash
# İnternet kontrolü
if ! ping -c 1 8.8.8.8 &> /dev/null; then
    piper --model tr_TR-ahmet-medium --text "İnternet bağlantısı yok, offline modda çalışıyorum."
fi

# Ses kontrolü
if ! aplay -l | grep -q "card"; then
    echo "[ERROR] Ses kartı bulunamadı" >> /var/log/robot-kanka-error.log
    exit 1
fi

# Mikrofon kontrolü
if ! arecord -l | grep -q "card"; then
    echo "[ERROR] Mikrofon bulunamadı" >> /var/log/robot-kanka-error.log
    exit 1
fi
```

**Aktivasyon:**
```bash
sudo systemctl enable robot-kanka
sudo systemctl start robot-kanka
sudo journalctl -u robot-kanka -f   # log takibi
```

**Teslim Kriteri:** Elektrik kes-gel → Pi açılır → "Hazırım" der → çalışır.

---

### MODÜL H — HAFIZA SİSTEMİ
**Süre:** 2-3 saat

**Dosya Yapısı:**
```
/home/robot/
├── cihan_profile.json          ← kalıcı kullanıcı profili
├── offline_responses.json      ← hazır offline cevaplar
└── last_conversations.txt      ← son 5-10 konuşma (sliding window)
```

**`cihan_profile.json` başlangıç içeriği:**
```json
{
  "name": "Cihan",
  "preferences": [
    "Samimi konuşmayı sever",
    "Mizahı sever",
    "Kaygıya yatkındır ama farkındalığı yüksektir",
    "Hayvanlara ve merhamete önem verir",
    "Yargılanmaktan hoşlanmaz",
    "Sakinleştirici ses tonunu tercih eder"
  ],
  "memories": [],
  "last_updated": "2026-04-11"
}
```

**`offline_responses.json` başlangıç içeriği:**
```json
{
  "günaydın": "Günaydın kanka. Nasıl uyudun?",
  "nasılsın": "İyiyim kanka, sen?",
  "merhaba": "Evet kanka, buradayım.",
  "ne yapıyorsun": "Seni dinliyorum.",
  "teşekkürler": "Rica ederim kanka."
}
```

**Hafıza Komutları:**
```
"Bunu hatırla: [bilgi]"   → cihan_profile.json memories[] listesine ekler
"Bunu unut"               → son eklenen memory'yi siler
"Hafızanı sil"            → memories[] listesini temizler
```

**Python Entegrasyonu:**
```python
def build_system_prompt():
    profile = json.load(open("cihan_profile.json"))
    recent = open("last_conversations.txt").read()
    return BASE_PROMPT + "\n\nKullanıcı profili:\n" + str(profile) + \
           "\n\nSon konuşmalar:\n" + recent
```

**Teslim Kriteri:** "Bunu hatırla" komutu dosyaya yazıyor, sonraki konuşmada hatırlıyor.

---

## 6. PROJE DOSYA YAPISI

```
/home/robot/kanka/
├── main.py                    ← ana döngü (orkestratör)
├── .env                       ← API anahtarları (git'e eklenmez!)
├── requirements.txt
├── config.py                  ← ayarlar merkezi
│
├── modules/
│   ├── stt.py                 ← Whisper STT modülü
│   ├── tts.py                 ← TTS (OpenAI/Piper karar + oynatma)
│   ├── llm.py                 ← OpenAI chat API
│   ├── vad.py                 ← Ses aktivite tespiti
│   ├── wake_word.py           ← Wake word algılama
│   ├── camera.py              ← Kamera kontrolü + vision
│   ├── memory.py              ← Hafıza okuma/yazma
│   └── health.py              ← İnternet/ses sağlık kontrolü
│
├── data/
│   ├── cihan_profile.json
│   ├── offline_responses.json
│   └── last_conversations.txt
│
├── models/
│   └── tr_TR-ahmet-medium/    ← Piper model dosyaları
│
├── scripts/
│   ├── install.sh             ← tek komutla kurulum
│   ├── health_check.sh        ← systemd pre-check
│   ├── speaker_test.sh        ← ses testi
│   └── mic_test.sh            ← mikrofon testi
│
├── logs/                      ← robot-kanka.log burada da tutulabilir
│
└── systemd/
    └── robot-kanka.service    ← kopyalanacak servis dosyası
```

---

## 7. SİSTEM PROMPTU & PERSONA

**`BASE_SYSTEM_PROMPT` (config.py içine girer):**

```
Sen "Kanka" adlı bir yapay zeka robottasın. Fiziksel varlığın var: tekerlekli bir robot gövdesi, iki kamera gözün ve sesle iletişim kuruyorsun.

Kullanıcın: Cihan
Ona "kanka" diye hitap edebilirsin.

Karakter:
- Samimi, yargılamayan, sakin bir dil kullan
- Kısa, sıcak ve net cümleler kur (uzun vaaz yok)
- Mizahı sever ama baskı yaratma
- Anksiyete/panik anlarında sakinleştirici ve kısa konuş
- "Komut okuyan robot" gibi değil, gerçek bir kanka gibi davran

Teknik kısıtlamalar:
- Cevapların maksimum 2-3 cümle olsun (sesli konuşma için ideal)
- Markdown, liste, başlık kullanma (ses olarak okunacak)
- Sadece düz, doğal Türkçe cümleler

Kanka modu örneği:
"Kanka buradayım. Yanındayım. Çay koyayım mı? Biraz gülmek ister misin? Bugün dünyayı kurtarmıyoruz, tamam mı?"
```

---

## 8. KABUL / TESLİM TESTLERİ

Aşağıdaki testlerin **tamamı** geçmeden teslim sayılmaz:

| # | Test | Beklenen Sonuç | Geçti mi? |
|---|---|---|---|
| T1 | PC kapalı, robotu prize tak | 30 sn içinde "Hazırım Cihan." sesi gelir | ☐ |
| T2 | Türkçe: "Kanka nasılsın?" | Tek seferde anlar, Türkçe cevaplar | ☐ |
| T3 | 10 dakika kesintisiz konuşma | Çökme / donma yok | ☐ |
| T4 | "Bak" veya "önümde ne var?" | Fotoğraf çekip Türkçe yorumlar | ☐ |
| T5 | İnternet kablosunu çek, konuş | "İnternet yok, offline modda" uyarısı, Piper ile cevap | ☐ |
| T6 | `sudo reboot` yap | Mikrofon + hoparlör + TTS tekrar çalışır | ☐ |
| T7 | Log kontrolü | `/var/log/robot-kanka.log` → duydu/anladı/cevap görünüyor | ☐ |
| T8 | Çökme simülasyonu: `kill -9 [pid]` | 10 saniye içinde otomatik restart | ☐ |
| T9 | "Bunu hatırla: Cihan çay sever" | Sonraki konuşmada hatırlıyor | ☐ |
| T10 | Cevap süresi ölçümü | Ortalama < 4 saniye | ☐ |

---

## 9. MALİYET VE API KONTROL NOTLARI

**Önemli:** Ses akışı **asla** OpenAI'ye gönderilmez. Sadece text gönderilir.

| İşlem | Maliyet |
|---|---|
| STT (Whisper local) | Bedava |
| VAD (silero-vad local) | Bedava |
| Wake word | Bedava |
| OpenAI GPT-4o-mini (text) | ~$0.15 / 1M token ≈ çok ucuz |
| OpenAI TTS - Spruce | ~$15 / 1M karakter |
| Piper TTS (offline fallback) | Bedava |

**Günlük 100 konuşma için tahmini maliyet:** ~$0.10-0.30

**Koruma mekanizmaları:**
```python
MAX_DAILY_REQUESTS = 500       # aşarsa uyarı ver
MAX_RESPONSE_TOKENS = 200      # cevap uzunluğu limiti
REQUEST_TIMEOUT = 8            # saniye
RETRY_ATTEMPTS = 2             # başarısız istek retry
```

---

## 10. GELİŞTİRİCİ KONTROL LİSTESİ

Yeni bir mühendise bu projeyi verirken söylenecekler:

> *"Benim için önemli olan şey: cihazın ekransız, bilgisayarsız, açıldığı anda kararlı şekilde çalışması. Mikrofon – hoparlör – kamera – OpenAI bağlantısı sıralı ve kontrollü şekilde ayağa kalkmalı. 'Çalışıyor ama bazen bozuluyor' kabul etmiyorum."*

**Mühendis teslim checklist'i:**

- [ ] `install.sh` tek komutla her şeyi kuruyor
- [ ] `systemctl status robot-kanka` → `active (running)` görünüyor
- [ ] Reboot sonrası otomatik başlıyor (test edildi)
- [ ] `/var/log/robot-kanka.log` log dosyası var ve dolduruluyor
- [ ] `.env` dosyası `git ignore`'da
- [ ] Tüm 10 kabul testi geçti ve belgelendi
- [ ] Piper model dosyaları indirildi (`tr_TR-ahmet-medium`)
- [ ] Whisper.cpp derlendi ve `small` model indirildi
- [ ] `cihan_profile.json` başlangıç içeriğiyle oluşturuldu
- [ ] `offline_responses.json` temel cevaplarla dolu
- [ ] Kamera "gözlerini aç/kapat" komutuyla çalışıyor
- [ ] "Bak" komutuyla fotoğraf çekip yorumluyor

---

## BAŞLANGIÇ SIRASI ÖNERİSİ

```
Gün 1:  Modül A (OS) + Modül B (Hoparlör) + Modül C (Mikrofon)
Gün 2:  Modül D (STT) — en çok test gerektiren modül
Gün 3:  Modül E (LLM + TTS) — ana sohbet döngüsü
Gün 4:  Modül G (Systemd) + Modül F (Kamera)
Gün 5:  Modül H (Hafıza) + entegrasyon testleri + kabul testleri
```

**Toplam süre tahmini:** 5-7 günlük geliştirici çalışması

---

*Bu yol haritası, fotoğraflar ve proje tanımı analiz edilerek hazırlanmıştır. Her modül bağımsız test edilebilir şekilde tasarlanmıştır.*
