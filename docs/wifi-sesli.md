# Sesli WiFi modu

Robot Kanka, yakındaki WiFi ağlarını sesle listeler; numara veya ağ adıyla seçim yapar. Açık ağlara şifresiz, şifrelilere sesle söylediğiniz şifreyle bağlanır.

## Kurulum (Raspberry Pi / Ubuntu)

```bash
sudo apt update
sudo apt install -y network-manager
bash scripts/setup_wifi_sudo.sh          # varsayılan: whoami
# servis kullanıcısı için:
bash scripts/setup_wifi_sudo.sh rblocal3
```

Test:

```bash
sudo -n nmcli dev wifi list
```

`.env`:

```
WIFI_ENABLED=1
```

Yerel STT (whisper-server veya cli) önerilir — internet yokken de şifre turu çalışır.

## Komutlar

| Ne dersiniz | Sonuç |
|-------------|--------|
| *wifi modunu aç* / *wifi ağlarını listele* | Tarama + numaralı liste |
| *iki numaraya bağlan* / *EvWiFi'ye bağlan* | Seçim |
| (şifreli ağ) şifreyi söyle | Bağlantı |
| *yeniden tara* | Listeyi yenile |
| *wifi modunu kapat* | Oturumu kapat |

Bluetooth kulaklık modu açıkken WiFi modu açılmaz (ve tersi).

## Güvenlik

- Şifre **diyalog hafızasına ve STT ipucuna yazılmaz**; logda `[wifi şifre girişi]` görünür.
- Karmaşık şifrelerde STT hata yapabilir; `WIFI_PASSWORD_CONFIRM=1` ile karakter sayısı onayı açılabilir.
- Gizli SSID, 802.1X ve captive portal bu sürümde desteklenmez.

## Sorun giderme

| Belirti | Çözüm |
|---------|--------|
| *WiFi yönetimi kurulu değil* | `network-manager` kurun; `nmcli general status` |
| Tarama boş | Anten/konum; `nmcli dev wifi rescan` |
| Bağlanamıyor / yetki | `bash scripts/setup_wifi_sudo.sh` |
| Şifre hep yanlış | Tekrar söyleyin; gerekirse `nmcli` ile klavyeden deneyin |

Backend: önce **nmcli** (NetworkManager), yoksa **wpa_cli** (`WIFI_WPA_INTERFACE`, varsayılan `wlan0`).
