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
network.target
sound.target
bluetooth.target
bluealsa.service
speaker-enable.service
        ↓
health_check.sh (max 60 sn bekleme; isteğe ping)
        ↓
robot-kanka.service → main.py
```

| Servis | Amaç |
|--------|------|
| `network.target` | Temel ağ yığını (boot'ta güvenilir; `network-online` çoğu Pi'de pasif kalır) |
| `sound.target` | ALSA |
| `bluetooth.target` | Kulaklık modu |
| `bluealsa.service` | BT ses (`BLUETOOTH_ENABLED=1` ise) |
| `speaker-enable.service` | Robot HAT hoparlörü |

## Ortam değişkenleri (.env)

| Değişken | Varsayılan | Açıklama |
|----------|------------|----------|
| `BOOT_DELAY_SEC` | `0` | İlk kontrolden önce ekstra bekleme (USB/ses kartı gecikmesi için 10–15) |
| `MIC_SETTLE_SEC` | `0` | Mikrofon `arecord -l`'de göründükten sonra ek bekleme (USB enumerate — serviste 8–15 deneyin) |
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

## Sesli tam kapanma

Robot çalışırken *"kanka robotu tamamen kapat"* (veya *"robotu tamamen kapat"*) deyince kısa bir veda cümlesi duyulur, ardından `sudo -n poweroff` ile Pi kapanır. Kritik pil kapanmasıyla aynı komut kullanılır.

**Şifre soruyorsa** (log: `password for …` veya `poweroff başarısız`), servis kullanıcısı için bir kerelik:

```bash
cd /home/KULLANICI/proje-dizini
bash scripts/setup_poweroff_sudo.sh    # varsayılan: whoami
# veya açık kullanıcı adı:
bash scripts/setup_poweroff_sudo.sh rblocal3
```

Test (çıktı `OK`, şifre istememeli):

```bash
sudo -n poweroff --help
```

`scripts/install.sh` bu adımı kurulumda otomatik dener.

*"Görüşürüz kanka"* yalnızca konuşma modunu kapatır; sistemi kapatmaz.

## Test

- **T1:** Prize tak → ~30 sn içinde açılış anonsu (`STARTUP_PHRASE`)
- **T6:** `sudo reboot` → ses + mikrofon + TTS tekrar çalışır
- **T8:** `sudo kill -9 $(pgrep -f 'venv/bin/python.*main.py')` → ~10 sn içinde yeniden başlar

Kabul listesi: `scripts/acceptance_checklist.txt`

## Sorun giderme

**Boot'ta `inactive`, elle `start` çalışıyor**

`list-dependencies` içinde `○ robot-kanka.service` ve `journalctl -b -u robot-kanka` boşsa iki yaygın neden:

1. **`speaker-enable` içinde `After=multi-user.target`** veya **`robot-kanka` içinde `Requires=speaker-enable`** — boot'ta `journalctl -b -u robot-kanka` boş kalır. Güncel dosyalar: `speaker-enable` → `Before=robot-kanka`; `robot-kanka` → `After=multi-user.target`, `Wants=` (Requires değil).

2. **`Wants=network-online.target`** — güncel unit `network.target` kullanır.

Pi'de her iki unit'i yeniden kurun:

```bash
cd ~/RaspberryRobot
PI_USER="$(whoami)" ROOT="$(pwd)"
sudo cp systemd/speaker-enable.service /etc/systemd/system/
sed -e "s|@USER@|${PI_USER}|g" -e "s|@ROOT@|${ROOT}|g" \
  systemd/robot-kanka.service | sudo tee /etc/systemd/system/robot-kanka.service
sudo systemctl daemon-reload
sudo systemctl enable speaker-enable robot-kanka
sudo reboot
```

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
HEALTH_WAIT_MAX_SEC=120
MIC_SETTLE_SEC=12
```

Güncel `robot-kanka.service` systemd tarafında `SupplementaryGroups=audio` kullanır; SSH'da mikrofon varken serviste `arecord -l boş` görüyorsanız önce `sudo cp systemd/robot-kanka.service` ile unit'i yeniden kurup `daemon-reload` yapın ve `MIC_SETTLE_SEC` artırın.

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
