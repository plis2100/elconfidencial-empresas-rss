import html
import os
import sys
import urllib.request
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import feedparser


URL_ORIGEN = "https://rss.elconfidencial.com/empresas/"
URL_WEB = "https://www.elconfidencial.com/empresas/"
ARCHIVO_RSS = Path("rss.xml")
MAX_ARTICULOS = 1500
ZONA_HORARIA = ZoneInfo("Europe/Madrid")


def dentro_del_horario():
    """
    Los lanzamientos manuales siempre se ejecutan.
    Los automáticos solamente entre las 07:00 y las 22:59,
    de lunes a sábado, hora española.
    """
    evento = os.environ.get("GITHUB_EVENT_NAME", "")

    if evento == "workflow_dispatch":
        return True

    ahora = datetime.now(ZONA_HORARIA)

    # Monday=0 ... Sunday=6
    if ahora.weekday() == 6:
        print("Domingo: no se actualiza el RSS.")
        return False

    if not 7 <= ahora.hour <= 22:
        print("Fuera del horario de 07:00 a 22:59, hora española.")
        return False

    return True


def descargar_feed():
    cabeceras = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0 Safari/537.36"
        ),
        "Accept": (
            "application/rss+xml, application/atom+xml, "
            "application/xml, text/xml;q=0.9, */*;q=0.8"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
    }

    solicitud = urllib.request.Request(URL_ORIGEN, headers=cabeceras)

    with urllib.request.urlopen(solicitud, timeout=40) as respuesta:
        contenido = respuesta.read()

    if not contenido:
        raise RuntimeError("El feed oficial se descargó vacío.")

    feed = feedparser.parse(contenido)

    if feed.bozo and not feed.entries:
        raise RuntimeError(f"No se pudo interpretar el feed: {feed.bozo_exception}")

    if not feed.entries:
        raise RuntimeError("El feed oficial no contiene noticias.")

    print(f"Noticias descargadas del feed oficial: {len(feed.entries)}")
    return feed.entries


def fecha_rss(entrada):
    for campo in ("published_parsed", "updated_parsed", "created_parsed"):
        valor = entrada.get(campo)

        if valor:
            fecha = datetime(
                valor.tm_year,
                valor.tm_mon,
                valor.tm_mday,
                valor.tm_hour,
                valor.tm_min,
                valor.tm_sec,
                tzinfo=ZoneInfo("UTC"),
            )
            return format_datetime(fecha)

    for campo in ("published", "updated", "created"):
        valor = entrada.get(campo)

        if valor:
            try:
                fecha = parsedate_to_datetime(valor)

                if fecha.tzinfo is None:
                    fecha = fecha.replace(tzinfo=ZoneInfo("UTC"))

                return format_datetime(fecha)
            except (TypeError, ValueError):
                pass

    return format_datetime(datetime.now(ZONA_HORARIA))


def obtener_imagen(entrada):
    media_content = entrada.get("media_content", [])

    for elemento in media_content:
        url = elemento.get("url", "")
        tipo = elemento.get("type", "")

        if url and (tipo.startswith("image/") or not tipo):
            return url

    media_thumbnail = entrada.get("media_thumbnail", [])

    for elemento in media_thumbnail:
        url = elemento.get("url", "")

        if url:
            return url

    for enlace in entrada.get("links", []):
        tipo = enlace.get("type", "")
        relacion = enlace.get("rel", "")
        url = enlace.get("href", "")

        if url and (tipo.startswith("image/") or relacion == "enclosure"):
            return url

    return ""


def limpiar_texto(valor):
    if valor is None:
        return ""

    return str(valor).strip()


def convertir_entrada(entrada):
    titulo = limpiar_texto(entrada.get("title", "Sin título"))
    enlace = limpiar_texto(entrada.get("link", ""))

    identificador = limpiar_texto(
        entrada.get("id")
        or entrada.get("guid")
        or enlace
        or titulo
    )

    descripcion = limpiar_texto(
        entrada.get("summary")
        or entrada.get("description")
        or entrada.get("content", [{}])[0].get("value", "")
    )

    autor = limpiar_texto(
        entrada.get("author")
        or entrada.get("dc_creator")
        or ""
    )

    categorias = []

    for etiqueta in entrada.get("tags", []):
        categoria = limpiar_texto(etiqueta.get("term", ""))

        if categoria and categoria not in categorias:
            categorias.append(categoria)

    return {
        "title": titulo,
        "link": enlace,
        "guid": identificador,
        "pubDate": fecha_rss(entrada),
        "description": descripcion,
        "author": autor,
        "categories": categorias,
        "image": obtener_imagen(entrada),
    }


def leer_articulos_anteriores():
    if not ARCHIVO_RSS.exists():
        return []

    try:
        raiz = ET.parse(ARCHIVO_RSS).getroot()
    except ET.ParseError:
        print("El rss.xml anterior no era válido; se reconstruirá.")
        return []

    articulos = []

    for item in raiz.findall("./channel/item"):
        categorias = [
            limpiar_texto(elemento.text)
            for elemento in item.findall("category")
            if limpiar_texto(elemento.text)
        ]

        enclosure = item.find("enclosure")

        imagen = ""
        if enclosure is not None:
            imagen = limpiar_texto(enclosure.get("url", ""))

        articulos.append(
            {
                "title": limpiar_texto(item.findtext("title")),
                "link": limpiar_texto(item.findtext("link")),
                "guid": limpiar_texto(item.findtext("guid")),
                "pubDate": limpiar_texto(item.findtext("pubDate")),
                "description": limpiar_texto(item.findtext("description")),
                "author": limpiar_texto(item.findtext("author")),
                "categories": categorias,
                "image": imagen,
            }
        )

    print(f"Noticias conservadas del RSS anterior: {len(articulos)}")
    return articulos


def clave_articulo(articulo):
    return (
        articulo.get("guid")
        or articulo.get("link")
        or articulo.get("title")
    ).strip()


def combinar_articulos(nuevos, anteriores):
    resultado = []
    identificadores = set()

    for articulo in nuevos + anteriores:
        clave = clave_articulo(articulo)

        if not clave or clave in identificadores:
            continue

        identificadores.add(clave)
        resultado.append(articulo)

        if len(resultado) >= MAX_ARTICULOS:
            break

    return resultado


def añadir_texto(padre, nombre, valor):
    elemento = ET.SubElement(padre, nombre)
    elemento.text = limpiar_texto(valor)
    return elemento


def crear_rss(articulos):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
            "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
        },
    )

    canal = ET.SubElement(rss, "channel")

    añadir_texto(canal, "title", "El Confidencial — Empresas")
    añadir_texto(canal, "link", URL_WEB)
    añadir_texto(
        canal,
        "description",
        "Todas las noticias de la sección Empresas de El Confidencial.",
    )
    añadir_texto(canal, "language", "es")
    añadir_texto(
        canal,
        "lastBuildDate",
        format_datetime(datetime.now(ZONA_HORARIA)),
    )
    añadir_texto(canal, "generator", "GitHub Actions RSS Generator")

    atom_link = ET.SubElement(
        canal,
        "{http://www.w3.org/2005/Atom}link",
    )
    atom_link.set(
        "href",
        "https://raw.githubusercontent.com/"
        "plis2100/elconfidencial-empresas-rss/main/rss.xml",
    )
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for articulo in articulos:
        item = ET.SubElement(canal, "item")

        añadir_texto(item, "title", articulo["title"])
        añadir_texto(item, "link", articulo["link"])

        guid = añadir_texto(item, "guid", articulo["guid"])
        guid.set("isPermaLink", "false")

        añadir_texto(item, "pubDate", articulo["pubDate"])
        añadir_texto(item, "description", articulo["description"])

        if articulo["author"]:
            añadir_texto(item, "author", articulo["author"])

        for categoria in articulo["categories"]:
            añadir_texto(item, "category", categoria)

        if articulo["image"]:
            enclosure = ET.SubElement(item, "enclosure")
            enclosure.set("url", articulo["image"])
            enclosure.set("type", "image/jpeg")

    arbol = ET.ElementTree(rss)
    ET.indent(arbol, space="  ")

    arbol.write(
        ARCHIVO_RSS,
        encoding="utf-8",
        xml_declaration=True,
    )


def main():
    if not dentro_del_horario():
        return

    entradas = descargar_feed()
    nuevos = [convertir_entrada(entrada) for entrada in entradas]
    anteriores = leer_articulos_anteriores()
    articulos = combinar_articulos(nuevos, anteriores)

    if not articulos:
        raise RuntimeError(
            "No se ha localizado ninguna noticia. "
            "Se cancela la actualización para no publicar un RSS vacío."
        )

    crear_rss(articulos)
    print(f"RSS creado correctamente con {len(articulos)} noticias.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
