# Sesli Bluetooth kulaklık modu

## Kurulum (Raspberry Pi)

```bash
bash scripts/setup_bluetooth_audio.sh
# veya tam kurulum:
bash scripts/install.sh
```

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
| kanka bluetooth kulaklık modunu aç | BT aç, cihazları numarala ve sesli oku |
| kanka 2 numaraya bağlan | 2. cihaza bağlan, ses kulaklıktan |
| kanka bluetooth kulaklık modunu kapat | Hoparlöre dön, BT kapat |

`hey kanka` ile konuşma modu açıkken ekstra wake gerekmez.

## Test (Pi)

1. `aplay -D hb` — robot hoparlörü
2. `systemctl status bluealsa`
3. Servisi çalıştır, yukarıdaki üç komutu sırayla dene
4. `bluetoothctl show` → Powered: no (kapatma sonrası)

Yerel komut eşleme testi (geliştirme makinesi):

```bash
python3 scripts/bt_session_smoke.py
```
