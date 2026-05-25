# Otomatik başlatma (systemd)

Pi 5 her açıldığında Robot Kanka, bağımlı servisler hazır olduktan sonra `venv/bin/python main.py` ile otomatik çalışır. Manuel `source venv/bin/activate` gerekmez.

## Hızlı kurulum

Proje dizininde (venv ve `.env` hazır):

```bash
bash scripts/install.sh
```

Kurulum sonunda:

- `speaker-enable.service` — hoparlör amplifikatörü (GPIO 20)
- `robot-kanka.service` — ana uygulama (mevcut kullanıcı ve proje yolu ile üretilir)

## Elle kurulum

```bash
cd /home/KULLANICI/proje-dizini   # gerçek yolunuz

# 1. Hoparlör
sudo cp systemd/speaker-enable.service /etc/systemd/system/
sudo systemctl enable --now speaker-enable

# 2. Ağ bekleme (Pi OS — bir kerelik)
sudo systemctl enable NetworkManager-wait-online.service
# veya: sudo systemctl enable systemd-networkd-wait-online.service

# 3. Ana servis (@USER@ ve @ROOT@ yer tutucuları doldurulur)
PI_USER="$(whoami)"
ROOT="$(pwd)"
sed -e "s|@USER@|${PI_USER}|g" -e "s|@ROOT@|${ROOT}|g" \
  systemd/robot-kanka.service | sudo tee /etc/systemd/system/robot-kanka.service

sudo systemctl daemon-reload
sudo systemctl enable robot-kanka
sudo systemctl start robot-kanka
```

## Bağımlılık sırası

```
network-online.target
sound.target
bluetooth.target
bluealsa.service
speaker-enable.service
        ↓
health_check.sh (max 60 sn bekleme)
        ↓
robot-kanka.service → main.py
```

| Servis | Amaç |
|--------|------|
| `network-online.target` | Ağ hazır (STT/TTS API) |
| `sound.target` | ALSA |
| `bluetooth.target` | Kulaklık modu |
| `bluealsa.service` | BT ses (`BLUETOOTH_ENABLED=1` ise) |
| `speaker-enable.service` | Robot HAT hoparlörü |

## Ortam değişkenleri (.env)

| Değişken | Varsayılan | Açıklama |
|----------|------------|----------|
| `BOOT_DELAY_SEC` | `0` | İlk kontrolden önce ekstra bekleme (USB/ses kartı gecikmesi için 10–15) |
| `HEALTH_WAIT_MAX_SEC` | `60` | Ses/mikrofon/servisler için max bekleme |
| `HEALTH_WAIT_INTERVAL_SEC` | `2` | Kontrol aralığı |
| `BLUETOOTH_ENABLED` | `1` | `1` ise `bluealsa.service` aktif olana kadar bekler |

## Log ve durum

```bash
systemctl status robot-kanka
journalctl -u robot-kanka -f
tail -f /var/log/robot-kanka.log
tail -f logs/robot-kanka-app.log
tail -f logs/health_err.log
```

## Test

- **T1:** Prize tak → ~30 sn içinde açılış anonsu (`STARTUP_PHRASE`)
- **T6:** `sudo reboot` → ses + mikrofon + TTS tekrar çalışır
- **T8:** `sudo kill -9 $(pgrep -f 'venv/bin/python.*main.py')` → ~10 sn içinde yeniden başlar

Kabul listesi: `scripts/acceptance_checklist.txt`

## Sorun giderme

**Servis hemen düşüyor**

```bash
journalctl -u robot-kanka -n 50 --no-pager
cat logs/health_err.log
aplay -l && arecord -l
systemctl status speaker-enable bluealsa
```

**Ses kartı geç geliyor**

`.env` içine ekleyin:

```
BOOT_DELAY_SEC=15
```

**Kullanıcı izinleri (GPIO/I2C/ses)**

```bash
sudo usermod -aG gpio,i2c,spi,audio "$USER"
# oturumu kapatıp tekrar açın veya reboot
```

**Servisi durdur / devre dışı**

```bash
sudo systemctl stop robot-kanka
sudo systemctl disable robot-kanka
```
