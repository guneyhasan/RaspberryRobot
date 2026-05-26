# Genel konuşma barge-in

Robot cevap okurken kullanıcı konuşursa TTS kesilir ve yeni söz normal tur olarak işlenir (intent / LLM).

Bluetooth liste modundaki barge-in ayrıdır (`BLUETOOTH_LIST_BARGE_IN`).

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|----------|------------|----------|
| `BARGE_IN_ENABLED` | `1` | Ana döngüde barge-in |
| `BARGE_IN_LISTEN_SEC` | `2.0` | TTS sırasında her dinleme dilimi (saniye) |
| `BARGE_IN_MIN_CHARS` | `4` | Çok kısa yankıları yoksay |
| `BARGE_IN_ONLY_CONVERSATION_MODE` | `1` | Yalnızca konuşma modu açıkken |
| `BARGE_IN_VAD_THRESHOLD` | boş | Doluysa barge-in dinlemesinde VAD eşiği (ör. `0.55`) |

## Sahte kesinti (robot sesi mikrofona girerse)

- Hoparlör ile mikrofonu fiziksel olarak ayırın veya kısık sesle test edin.
- `BARGE_IN_VAD_THRESHOLD` ile eşiği yükseltin.
- `BARGE_IN_MIN_CHARS` değerini artırın.

## Davranış

- Kesinti sonrası yarım kalan asistan cevabı belleğe yazılmaz; yalnızca tamamlanan turlar kaydedilir.
- Açılış anonsu, pil uyarısı ve kamera watchdog TTS barge-in kullanmaz.
