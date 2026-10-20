"""Transformation: Claude picks what to share and writes the commentary that goes with it."""
from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel, Field

from .ccvideo import CCVideo
from .config import Config
from .sources import Item

log = logging.getLogger(__name__)


class XPost(BaseModel):
    publish: bool = Field(description="false si nada vale la pena compartir ahora")
    item_id: str | None = Field(description="id entre corchetes del contenido elegido")
    topic: str = Field(description="tema en 3-6 palabras, para no repetirlo")
    text: str = Field(description="comentario para X, máximo 230 caracteres incluyendo hashtags, sin enlaces")


class VideoCaption(BaseModel):
    publish: bool = Field(description="false si el video no trata de GTA VI o parece material oficial resubido")
    headline: str = Field(description="titular corto sobre el video, 3-7 palabras, se muestra encima del video")
    tiktok_caption: str = Field(description="descripción para TikTok, máximo 150 caracteres con 2-3 hashtags")
    x_text: str = Field(description="texto para X, máximo 180 caracteres con 1-2 hashtags, sin enlaces")


RULES = """Eres el editor de una cuenta de fans de GTA VI en X y TikTok. Escribes en {language}.
La cuenta comparte contenido ya creado por otros (posts de Rockstar y de cuentas de noticias, videos con
licencia Creative Commons) y le agrega valor con un comentario propio: contexto, por qué importa, una opinión
o una pregunta para la audiencia.

Reglas:
- Nunca te atribuyas el contenido; el crédito al autor lo agrega el bot automáticamente.
- Distingue lo confirmado por Rockstar de rumores y filtraciones; marca los rumores como tales.
- No inventes datos, fechas, precios ni citas. Si algo no está en la fuente, no lo afirmes.
- La cuenta es de fans y no oficial; no te hagas pasar por Rockstar.
- Tono cercano y enérgico, sin clickbait engañoso. Máximo 2-3 hashtags (#GTA6 primero).
- No repitas temas recientes: {recent}."""

PICK = """Elige el contenido más interesante para compartir ahora en X y escribe el comentario.
Prefiere, en este orden: posts y videos oficiales de Rockstar, posts de X de cuentas de noticias conocidas,
noticias de medios. Evita lo repetido o de poco interés. Si nada vale la pena, publish=false.

Contenido disponible:

{listing}"""

CAPTION = """Este video de YouTube tiene licencia Creative Commons (CC BY) y lo vamos a compartir recortado
en formato vertical, con crédito a su autor.

Título: {title}
Autor: {author}
Descripción: {description}

Escribe el titular, la descripción de TikTok y el texto para X."""


def _language(cfg: Config) -> str:
    return "español latino" if cfg.language.startswith("es") else cfg.language


def _ask(cfg: Config, system: str, prompt: str, schema: type[BaseModel]):
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    response = client.beta.messages.parse(
        model=cfg.model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_format=schema,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request")
    if response.parsed_output is None:
        raise RuntimeError(f"No structured output (stop_reason={response.stop_reason})")
    return response.parsed_output


def write_post(cfg: Config, items: list[Item], recent_topics: list[str]) -> XPost:
    if not items:
        return XPost(publish=False, item_id=None, topic="", text="")
    if not cfg.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not set: using the offline template writer")
        item = next((i for i in items if i.kind != "news"), items[0])
        return XPost(publish=True, item_id=item.id, topic=item.title[:40],
                     text=f"{item.title[:180]} ¿Qué opinas? #GTA6")
    system = RULES.format(language=_language(cfg), recent=", ".join(recent_topics) or "ninguno")
    listing = "\n\n".join(i.to_prompt() for i in items[:25])
    return _ask(cfg, system, PICK.format(listing=listing), XPost)


def write_caption(cfg: Config, video: CCVideo, recent_topics: list[str]) -> VideoCaption:
    if not cfg.anthropic_api_key:
        return VideoCaption(publish=True, headline=video.title[:60], tiktok_caption=f"{video.title[:110]} #GTA6 #GTAVI",
                            x_text=f"{video.title[:150]} #GTA6")
    system = RULES.format(language=_language(cfg), recent=", ".join(recent_topics) or "ninguno")
    prompt = CAPTION.format(title=video.title, author=video.author, description=video.description or "(sin descripción)")
    return _ask(cfg, system, prompt, VideoCaption)
