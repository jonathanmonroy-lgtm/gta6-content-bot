"""Transformation: turn raw news into original posts and a short video script with Claude."""
from __future__ import annotations

import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .config import Config
from .sources import Item

log = logging.getLogger(__name__)


class Scene(BaseModel):
    narration: str = Field(description="Lo que dice la voz en off en esta escena, 1-2 frases cortas")
    broll: str = Field(description="búsqueda EN INGLÉS de video de stock para el fondo, 2-4 palabras, "
                       "estilo Miami/Florida/neón/autos/playa/ciudad de noche; nunca nombres de juegos o marcas")


class Content(BaseModel):
    publish: bool = Field(description="false si nada vale la pena publicar ahora")
    mode: Literal["news", "evergreen"]
    source_item_id: str | None = Field(description="id entre corchetes del item usado, o null")
    topic: str = Field(description="tema en 3-6 palabras, para no repetirlo")
    tweet: str = Field(description="post para X, máximo 260 caracteres incluyendo hashtags")
    tiktok_caption: str = Field(description="descripción para TikTok, máximo 150 caracteres con hashtags")
    label: Literal["NOTICIA", "RUMOR", "CONFIRMADO", "FILTRACIÓN", "DATO", "TEORÍA"] = Field(
        description="etiqueta honesta del tipo de contenido")
    hook: str = Field(description="gancho en pantalla de los primeros 3 segundos, 3-7 palabras, impactante y veraz")
    title: str = Field(description="titular corto que queda arriba durante el video, máximo 6 palabras")
    scenes: list[Scene] = Field(description="5 a 8 escenas, 30-50 segundos de narración en total; "
                                "la primera escena dice el gancho en voz, la última invita a comentar y seguir")
    source_url: str | None = None


SYSTEM = """Eres el editor de una cuenta de fans de GTA VI en X y TikTok. Escribes en {language}.

Tu trabajo es transformar noticias y conversaciones de la comunidad en contenido ORIGINAL:
resúmenes con tus propias palabras, contexto, análisis, opinión y preguntas para la audiencia.

Reglas:
- Nunca copies frases textuales de la fuente; reescribe y aporta valor (contexto, por qué importa).
- Distingue hechos confirmados por Rockstar de rumores y filtraciones; marca los rumores como tales.
- No inventes datos, fechas, precios ni citas. Si algo no está en las fuentes, no lo afirmes.
- La cuenta es de fans y no oficial; no te hagas pasar por Rockstar.
- Tono: cercano, enérgico, sin clickbait engañoso. Máximo 2-3 hashtags relevantes (#GTA6 primero).
- Video: el gancho va en la primera escena; los primeros 2 segundos deciden si la gente se queda.
  Frases cortas y habladas, ritmo rápido, una idea por escena, sin relleno ni saludos.
  Escribe "GTA seis" en la narración (no "GTA VI") para que la voz lo pronuncie bien.
- Cierra el video con una pregunta concreta y una invitación a seguir {handle}.
- No repitas temas recientes: {recent}.

Si no hay noticias nuevas útiles y se permite modo evergreen, crea contenido evergreen basado SOLO en
información pública ya confirmada (tráilers oficiales, ubicación en Leonida/Vice City, protagonistas
Lucia y Jason, comparativas con juegos anteriores, preguntas a la comunidad). Si no hay nada bueno, publish=false."""


def build_prompt(items: list[Item], allow_evergreen: bool) -> str:
    if items:
        listing = "\n\n".join(i.to_prompt() for i in items[:15])
        body = f"Noticias y publicaciones nuevas (elige la más interesante y relevante):\n\n{listing}"
    else:
        body = "No hay noticias nuevas en este momento."
    evergreen = "Se permite modo evergreen." if allow_evergreen else "No uses modo evergreen."
    return f"{body}\n\n{evergreen}\nGenera el contenido."


def generate(cfg: Config, items: list[Item], recent_topics: list[str]) -> Content:
    if not cfg.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not set: using the offline template writer")
        return offline_content(items, cfg)

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    system = SYSTEM.format(
        language="español latino" if cfg.language.startswith("es") else cfg.language,
        handle=cfg.handle,
        recent=", ".join(recent_topics) or "ninguno",
    )
    response = client.beta.messages.parse(
        model=cfg.model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": build_prompt(items, cfg.evergreen_when_no_news)}],
        output_format=Content,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write this content")
    if response.parsed_output is None:
        raise RuntimeError(f"No structured output (stop_reason={response.stop_reason})")
    content = response.parsed_output
    if content.source_item_id and not content.source_url:
        match = next((i for i in items if i.id == content.source_item_id), None)
        content.source_url = match.url if match else None
    return content


def offline_content(items: list[Item], cfg: Config) -> Content:
    """Deterministic content for dry runs and tests when no API key is configured."""
    if not items:
        return Content(
            publish=False, mode="evergreen", source_item_id=None, topic="sin novedades",
            tweet="", tiktok_caption="", label="DATO", hook="", title="", scenes=[],
        )
    item = items[0]
    return Content(
        publish=True,
        mode="news",
        source_item_id=item.id,
        topic=item.title[:40],
        tweet=f"Novedad de GTA VI: {item.title[:180]} ¿Qué opinas? #GTA6",
        tiktok_caption=f"{item.title[:110]} #GTA6 #GTAVI",
        label="NOTICIA",
        hook="Lo nuevo de GTA VI",
        title="Lo nuevo de GTA VI",
        scenes=[
            Scene(narration="Esto es lo último que se sabe de GTA seis.", broll="miami night neon"),
            Scene(narration=item.title, broll="miami beach aerial"),
            Scene(narration="¿Tú qué opinas? Te leo en comentarios.", broll="sports car night city"),
        ],
        source_url=item.url,
    )
