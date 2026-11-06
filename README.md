# Bitget Volatility Radar

Radar de volatilidad para mercados de Bitget. Se conecta al WebSocket público de Bitget y envía avisos a Telegram cuando un activo sube o baja al menos el porcentaje configurado en una ventana móvil.

## Qué hace

- Umbral inicial: **±1 %**.
- Ventanas: **5 y 10 minutos**.
- Avisos alcistas y bajistas a Telegram.
- Conexión pública de solo lectura: no necesita API key de Bitget ni permisos de trading.
- Reconexión automática si se corta el WebSocket.
- Verifica al arrancar qué símbolos existen en el tipo de contrato seleccionado; los que no existan se muestran en el registro y se omiten.
- Enfriamiento por defecto de 5 minutos por activo, ventana y dirección para evitar avisos repetidos.

## 1. Preparar Telegram

1. Abre Telegram y entra en el chat de tu bot.
2. Pulsa **Iniciar** o envía `/start`.
3. Guarda el token de BotFather en un lugar privado. **No lo publiques en GitHub ni lo compartas en el chat.**
4. Para obtener tu identificador de chat, primero envía `/start` al bot y consulta el método `getUpdates` de Telegram Bot API desde tu ordenador. No publiques el token ni el resultado completo de la consulta.

## 2. Desplegar en Railway

1. Crea una cuenta en Railway e inicia sesión.
2. Elige **New Project → Deploy from GitHub Repo**.
3. Autoriza GitHub si te lo pide y selecciona `marcgiribet/bitget-volatility-radar`.
4. Si solicita un comando de inicio, usa:

   ```bash
   python main.py
   ```

5. En **Variables**, crea:
   - `TELEGRAM_BOT_TOKEN`: token privado de BotFather.
   - `TELEGRAM_CHAT_ID`: identificador de tu chat.
   - `ALERT_PERCENT`: `1`
   - `WINDOWS_MINUTES`: `5,10`
   - `ALERT_COOLDOWN_SECONDS`: `300`
   - `BITGET_PRODUCT_TYPE`: `USDT-FUTURES`
6. Pulsa **Deploy** y revisa los registros. Debe aparecer `Radar iniciado` y la lista de símbolos activos.

GitHub guarda el código, pero no ejecuta por sí solo un proceso permanente 24/7. Railway es un alojamiento independiente; comprueba sus precios vigentes antes de activar el servicio.

## 3. Lista de activos

El valor inicial de `SYMBOLS` incluye los símbolos base de la lista original. El programa elimina el sufijo visual `.P` y comprueba los contratos disponibles en Bitget. Si un símbolo como `XAUUSD` no existe en `USDT-FUTURES`, se omitirá: no se sustituye silenciosamente por otro instrumento.

Si un activo cotiza en otra categoría, primero hay que confirmar su identificador y tipo de producto en Bitget. Cambiar `BITGET_PRODUCT_TYPE` cambia la categoría consultada.

## Variables disponibles

| Variable | Valor por defecto | Uso |
|---|---:|---|
| `ALERT_PERCENT` | `1` | Umbral porcentual absoluto |
| `WINDOWS_MINUTES` | `5,10` | Ventanas móviles en minutos |
| `ALERT_COOLDOWN_SECONDS` | `300` | Pausa entre avisos repetidos |
| `BITGET_PRODUCT_TYPE` | `USDT-FUTURES` | Categoría de contratos Bitget |
| `SYMBOLS` | Lista del proyecto | Símbolos separados por comas |

## Importante

- Tras iniciar el servicio, cada ventana necesita acumular historial suficiente antes de calcular su variación. Al principio puede tardar hasta 10 minutos en generar alertas válidas.
- El precio se compara con el último dato disponible en torno al inicio de la ventana; la frecuencia de actualización y la liquidez pueden afectar al instante exacto del aviso.
- Un aviso de volatilidad no es una recomendación de compra o venta.
- No añadas tokens, claves API ni contraseñas a archivos del repositorio.
