# Bot de contenido GTA VI

Bot autónomo que cada 3 horas:

1. **Extrae** noticias y publicaciones de GTA VI (Google News en español e inglés, r/GTA6).
2. **Transforma** con Claude: elige la noticia más interesante y escribe contenido original
   (resumen con sus propias palabras, contexto, rumor vs. confirmado, pregunta a la audiencia).
   Si no hay noticias, crea contenido *evergreen* sobre información ya confirmada.
3. **Crea** un video vertical 1080x1920 de 25-45 s con voz en off (edge-tts) y gráficos propios,
   sin usar clips ni arte de Rockstar.
4. **Publica** el post en X (con el video adjunto) y el video en TikTok.

Corre gratis en **GitHub Actions** (`.github/workflows/bot.yml`); no hay servidor que mantener.
El estado (qué noticias ya se usaron, qué se publicó) se guarda en `data/state.json` y el
workflow lo commitea al terminar cada corrida. Cada corrida sube lo generado (texto + video)
como artifact del workflow durante 14 días.

## Modo prueba

Mientras `DRY_RUN` no sea `false`, el bot genera todo pero **no publica nada**. Cualquier
credencial que falte también deja ese paso en modo prueba, así que se puede activar por partes.

```bash
pip install -r requirements.txt -r requirements-dev.txt   # requiere ffmpeg
python -m pytest -q
python -m gtabot --dry-run --force-video                   # deja el resultado en output/
```

## Puesta en marcha

Todo se configura en el repo: **Settings → Secrets and variables → Actions**.

### 1. Claude (texto)
- Crea una API key en https://console.anthropic.com → secret `ANTHROPIC_API_KEY`.
- Sin key, el bot usa una plantilla básica (útil solo para pruebas).

### 2. X
1. En https://developer.x.com crea un proyecto y una app (el plan gratis permite ~500 posts/mes).
2. En *User authentication settings* pon permisos **Read and write**.
3. Genera *API Key/Secret* y luego *Access Token/Secret* **con la cuenta del bot** (después de activar escritura).
4. Secrets: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`.

### 3. TikTok
1. En https://developers.tiktok.com crea una app, agrega los productos **Login Kit** y
   **Content Posting API** (con *Direct Post* activado) y los scopes `video.publish`, `video.upload`.
2. Configura un *Redirect URI* (por ejemplo la URL del repo) y ponlo en `TIKTOK_REDIRECT_URI`.
3. Obtén el refresh token con la cuenta del bot:
   ```bash
   export TIKTOK_CLIENT_KEY=... TIKTOK_CLIENT_SECRET=... TIKTOK_REDIRECT_URI=...
   python scripts/tiktok_auth.py url          # abre el link e inicia sesión
   python scripts/tiktok_auth.py token CODIGO # el code que aparece en la URL de regreso
   ```
4. Secrets: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN`.
5. **Auditoría:** hasta que TikTok audite la app, los videos solo se publican como privados
   (`SELF_ONLY`). Pide la auditoría desde el portal y, cuando la aprueben, pon la variable
   `TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE`.
6. TikTok rota el refresh token. Para que el bot lo guarde solo, crea un *fine-grained token* de
   GitHub con permiso **Secrets: Read and write** sobre este repo → secret `GH_SECRETS_TOKEN`.

### 4. Activar
- Variable `DRY_RUN=false`.
- Opcionales: `BOT_HANDLE` (ej. `@TuCuenta`), `BOT_LANGUAGE` (`es`), `TTS_VOICE`
  (`es-MX-JorgeNeural`, `es-MX-DaliaNeural`, ...), `MAX_VIDEOS_PER_DAY` (3), `MAX_POSTS_PER_DAY` (8).
- Para probar al momento: pestaña **Actions → Bot → Run workflow**.

## Contenido y derechos
El material de GTA VI es de Rockstar Games. El bot solo publica contenido transformado
(resúmenes, análisis, comentario) con gráficos propios, cita la fuente, separa rumores de hechos
confirmados, se presenta como cuenta de fans no oficial y marca los videos como contenido generado
con IA en TikTok. Eso también es lo que piden X y TikTok para monetizar.

## Estructura
```
gtabot/sources.py      extracción (RSS)
gtabot/writer.py       transformación con Claude (salida estructurada)
gtabot/video.py        render del video (Pillow + ffmpeg + edge-tts)
gtabot/publishers/     X (API v2) y TikTok (Content Posting API)
gtabot/main.py         orquestación de una corrida
```
