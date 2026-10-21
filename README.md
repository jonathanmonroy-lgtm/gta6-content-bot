# Bot de contenido GTA VI

Bot autónomo que cada 3 horas reutiliza contenido ya creado sobre GTA VI, de forma permitida:

1. **X, citas con comentario.** Junta posts de X enlazados en noticias y en r/GTA6, videos del canal
   oficial de Rockstar en YouTube y noticias de Google News. Claude elige lo mejor y escribe un
   comentario propio. Los posts de X se **citan** (el original sigue acreditado a su autor) y lo demás
   se comparte con su enlace.
2. **Videos Creative Commons para TikTok y X.** Busca en YouTube videos de GTA VI con licencia
   **CC BY**, revisa la licencia en cada video, descarga solo el tramo que usa (máx. 58 s), lo pasa a
   vertical 1080x1920 con fondo difuminado y un titular, y pone el crédito al autor en el video y en
   la descripción. Se salta resubidas de tráilers oficiales, que reciben reclamos aunque digan CC.

Corre gratis en **GitHub Actions** (`.github/workflows/bot.yml`); no hay servidor que mantener.
El estado (qué ya se compartió) se guarda en `data/state.json`, que el workflow commitea en cada corrida.

## Modo prueba

Mientras `DRY_RUN` no sea `false`, el bot genera todo pero **no publica nada**. Cualquier
credencial que falte también deja ese paso en modo prueba, así que se puede activar por partes.

```bash
pip install -r requirements.txt -r requirements-dev.txt   # requiere ffmpeg
python -m pytest -q
python -m gtabot --dry-run --force-video                   # deja el resultado en output/ (requiere deno para YouTube)
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

### 4. YouTube (cookies)
YouTube bloquea las descargas desde los servidores de GitHub ("confirma que no eres un bot").
El bot usa la sesión de una **cuenta secundaria** de YouTube (nunca la principal):
1. En una ventana privada del navegador inicia sesión en youtube.com con esa cuenta.
2. Con la extensión *Get cookies.txt LOCALLY* exporta las cookies de youtube.com (formato Netscape)
   y cierra la ventana sin cerrar sesión.
3. Pega el contenido completo del archivo en el secret `YT_COOKIES`.
Si en los logs vuelve a aparecer el bloqueo, las cookies caducaron: repite los pasos.

### 5. Activar
- Variable `DRY_RUN=false`.
- Opcionales: `BOT_HANDLE` (ej. `@TuCuenta`), `BOT_LANGUAGE` (`es`), `MAX_VIDEOS_PER_DAY` (3), `MAX_POSTS_PER_DAY` (8).
- Para probar al momento: pestaña **Actions → Bot → Run workflow**.

## Contenido y derechos
El bot no resube videos ajenos sin permiso. En X cita el post original, y en TikTok solo usa videos
con licencia Creative Commons Attribution, que permite reutilizarlos dando crédito. Ojo: la licencia
CC solo cubre lo que es del autor. Si un video usa mucho material de Rockstar, igual puede recibir un
reclamo, por eso el bot descarta resubidas de tráilers oficiales.

## Estructura
```
gtabot/sources.py      noticias, posts de X enlazados y canales de YouTube (RSS)
gtabot/ccvideo.py      búsqueda de videos CC BY, recorte vertical con crédito
gtabot/writer.py       Claude elige qué compartir y escribe comentarios y descripciones
gtabot/publishers/     X (API v2, citas y video) y TikTok (Content Posting API)
gtabot/main.py         orquestación de una corrida
```
