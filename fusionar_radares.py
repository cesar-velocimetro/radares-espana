import json
import math
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

URL_RADARES_ANUNCIADOS = "https://github.io"
URL_NAP_DGT = "https://dgt.es"
ARCHIVO_OSM_LOCAL = "radares_osm.geojson"
ARCHIVO_SALIDA = "radares_unificados.json"

DISTANCIA_DEDUPLICACION_METROS = 45.0


def haversine_metros(lat1, lon1, lat2, lon2):
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extraer_velocidad(valor):
    if not valor:
        return None
    match = re.search(r"(\d{2,3})", str(valor))
    if match:
        vel = int(match.group(1))
        return vel if 20 <= vel <= 130 else None
    return None


def descargar_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "RadaresAppSync/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cargar_radares_anunciados():
    radares = []
    try:
        data = descargar_json(URL_RADARES_ANUNCIADOS)
        for feat in data.get("features", []):
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})
            coords = geom.get("coordinates", [])

            if geom.get("type") == "Point" and len(coords) >= 2:
                lon = float(coords[0])
                lat = float(coords[1])
                tipo_raw = str(props.get("type", "")).lower()
                tipo = "TRAMO" if "tramo" in tipo_raw else "FIJO"
                if "movil" in tipo_raw or "móvil" in tipo_raw:
                    tipo = "MOVIL"

                radares.append({
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed": extraer_velocidad(props.get("limit") or props.get("speed_limit")),
                    "type": tipo,
                    "source": props.get("source", "ANUNCIADOS")
                })
    except Exception as e:
        print(f"Aviso: No se pudo cargar Radares Anunciados: {e}")
    return radares


def cargar_nap_dgt():
    radares = []
    try:
        req = urllib.request.Request(URL_NAP_DGT, headers={"User-Agent": "RadaresAppSync/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_bytes = resp.read()

        root = ET.fromstring(xml_bytes)
        for elem in root.iter():
            if elem.tag.endswith("pointCoordinates"):
                lat, lon = None, None
                for child in elem:
                    if child.tag.endswith("latitude"):
                        lat = float(child.text)
                    elif child.tag.endswith("longitude"):
                        lon = float(child.text)
                if lat is not None and lon is not None:
                    radares.append({
                        "lat": round(lat, 6),
                        "lon": round(lon, 6),
                        "speed": None,
                        "type": "FIJO",
                        "source": "DGT_NAP"
                    })
    except Exception as e:
        print(f"Aviso: No se pudo cargar NAP DGT: {e}")
    return radares


def cargar_osm_local():
    radares = []
    try:
        with open(ARCHIVO_OSM_LOCAL, "r", encoding="utf-8") as f:
            data = json.load(f)

        for feat in data.get("features", []):
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})
            coords = geom.get("coordinates", [])
            if geom.get("type") == "Point" and len(coords) >= 2:
                lon = float(coords[0])
                lat = float(coords[1])
                velocidad = extraer_velocidad(props.get("maxspeed"))
                tipo = "SEMAFORO" if "traffic_signals" in str(props.get("enforcement", "")) else "FIJO"

                radares.append({
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed": velocidad,
                    "type": tipo,
                    "source": "OSM"
                })
    except Exception as e:
        print(f"Aviso: No se pudo leer el GeoJSON de OSM: {e}")
    return radares


def fusionar(listas_priorizadas):
    unificados = []
    for lista in listas_priorizadas:
        for candidato in lista:
            es_duplicado = False
            for existente in unificados:
                dist = haversine_metros(
                    existente["lat"], existente["lon"],
                    candidato["lat"], candidato["lon"]
                )
                if dist <= DISTANCIA_DEDUPLICACION_METROS:
                    es_duplicado = True
                    if existente["speed"] is None and candidato["speed"] is not None:
                        existente["speed"] = candidato["speed"]
                    break

            if not es_duplicado:
                candidato["id"] = len(unificados) + 1
                unificados.append(candidato)
    return unificados


if __name__ == "__main__":
    print("Descargando Radares Anunciados...")
    anunciados = cargar_radares_anunciados()
    print(f" -> {len(anunciados)} puntos")

    print("Descargando NAP DGT...")
    dgt = cargar_nap_dgt()
    print(f" -> {len(dgt)} puntos")

    print("Cargando extracto OSM...")
    osm = cargar_osm_local()
    print(f" -> {len(osm)} puntos")

    resultado = fusionar([anunciados, dgt, osm])
    print(f"Total radares únicos tras deduplicación: {len(resultado)}")

    salida = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(resultado),
        "radars": resultado
    }

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
