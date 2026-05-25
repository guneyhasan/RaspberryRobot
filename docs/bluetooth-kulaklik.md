# Sesli Bluetooth kulaklık modu

## Kurulum (Raspberry Pi)

```bash
bash scripts/setup_bluetooth_audio.sh
# veya tam kurulum:
bash scripts/install.sh
```

### `Package 'bluealsa' has no installation candidate`

Bu hata **sorun çıkarır**: Robot Kanka kulaklığa sesi `aplay -D bluealsa:DEV=...` ile verir; BlueALSA yoksa bağlansanız bile TTS kulaklıktan duyulmaz.

| Pi OS / Debian | Durum |
|----------------|--------|
| **11 Bullseye** | `bluealsa` ve `bluez-alsa-utils` çoğu repoda **yok** |
| **12 Bookworm** | Paket adı genelde **`bluez-alsa-utils`** (`apt install bluez-alsa-utils`) |
| **13+** | `bluez-alsa-utils` veya `bluealsa` |

Deneyin:

```bash
sudo apt update
sudo apt install bluez bluez-tools bluez-alsa-utils
sudo systemctl enable --now bluealsa
bluealsa-aplay -L
```

Bullseye'de kalmak zorundaysanız: OS'yi Bookworm'a yükseltin veya [bluez-alsa](https://github.com/arkq/bluez-alsa) kaynağından derleyin.

`.env` içinde:

```
AUDIO_OUTPUT_ALSA_DEVICE=hb
BLUETOOTH_ENABLED=1
```

PipeWire ile çakışma olursa kullanıcı oturumunda PipeWire'ı durdurun veya mask'leyin (`yapilacak adimlar.txt`).

## İlk eşleştirme (bir kerelik)

```bash
bluetoothctl
power on
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
```

## Sesli komutlar

| Komut | Sonuç |
|-------|--------|
| kanka bluetooth kulaklık modunu aç | Eşleşmiş varsa otomatik bağlan; yoksa tara ve listele |
| kanka 2 numaraya bağlan | 2. cihaza bağlan (gerekirse eşleştirir) |
| kanka 2 numaraya eşleştir | 2. cihazı pair + bağlan |
| yeniden tara / tekrar tara | Yakındaki cihazları yeniden listele |
| kanka bluetooth kulaklık modunu kapat | Hoparlöre dön, BT kapat |

`hey kanka` ile konuşma modu açıkken ekstra wake gerekmez.

## Tarama boş kalıyorsa

BlueZ 5.66+ `bluetoothctl` içinde `paired-devices` yoktur. Tarama **aynı oturumda** yapılmalı:

```bash
bluetoothctl
power on
scan on
# 10–15 sn bekle
devices
```

Güncel kod tek süreçte `scan on` → bekler → `devices` çalıştırır. Logda `BT tarama: ... merged=6` gibi bir sayı görmelisiniz.

## Test (Pi)

1. `aplay -D hb` — robot hoparlörü
2. `systemctl status bluealsa`
3. Servisi çalıştır, yukarıdaki üç komutu sırayla dene
4. `bluetoothctl show` → Powered: no (kapatma sonrası)

Yerel komut eşleme testi (geliştirme makinesi):

```bash
python3 scripts/bt_session_smoke.py
```
