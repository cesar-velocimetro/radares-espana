import json
import math
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

URL_RADARES_ANUNCIADOS = "https://" + "geiserx.github.io" + "/radares-anunciados-ha/feed.geojson"
URL_NAP_DGT = "https://" + "nap.dgt.es" + "/datex2/dgt/PredefinedLocationsPublication/radares/content.xml"
ARCHIVO_OSM_LOCAL = "radares_osm.geojson"
ARCHIVO_SALIDA = "radares_unificados.json"

DISTANCIA_DEDUPLICACION_METROS = 45.0

CARDINALES = {
    "n": 0, "norte": 0,
    "nne": 22,
    "ne": 45, "noreste": 45,
    "ene": 67,
    "e": 90, "este": 90,
    "ese": 112,
    "se": 135, "sureste": 135,
    "sse": 157,
    "s": 180, "sur": 180,
    "ssw": 202, "sso": 202,
    "sw": 225, "so": 225, "suroeste": 225,
    "wsw": 247, "oso": 247,
    "w": 270, "o": 270, "oeste": 270,
    "wnw": 292, "ono": 292,
    "nw": 315, "no": 315, "noroeste": 315,
    "nnw": 337, "nno": 337
}


def haversine_metros(lat1, lon1, lat2, lon2):
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def calcular_bearing(lat1, lon1, lat2, lon2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    angulo = math.degrees(math.atan2(y, x))
    return int(round((angulo + 360.0) % 360.0)) % 360


def diferencia_angular(b1, b2):
    diff = abs(b1 - b2) % 360
    return 360 - diff if diff > 180 else diff


def extraer_bearing(props):
    for clave in ("camera:direction", "direction", "bearing", "angle", "course", "heading", "sentido"):
        val = props.get(clave)
        if val is None:
            continue
        texto = str(val).strip().lower()
        # Si viene un rango como "90;270" o "forward;backward", tomamos el primer valor numérico si existe
        primero = texto.split(";").__getitem__(0).strip()
        if primero in CARDINALES:
            return CARDINALES.get(primero)
        match = re.search(r"(-?\d+(?:\.\d+)?)", primero)
        if match:
            deg = float(match.group(1))
            if -360.0 <= deg <= 360.0:
                return int(round((deg + 360.0) % 360.0)) % 360
    return None


def extraer_velocidad(props):
    texto = json.dumps(props, ensure_ascii=False)
    for clave in ("limit", "speed_limit", "maxspeed", "velocidad", "speed"):
        val = props.get(clave)
        if val:
            match = re.search(r"(\d{2,3})", str(val))
            if match:
                vel = int(match.group(1))
                if 20 <= vel <= 130:
                    return vel
    match = re.search(r"\b(30|40|50|60|70|80|90|100|110|120)\b", texto)
    if match:
        return int(match.group(1))
    return None


def clasificar_tipo(props):
    texto = json.dumps(props, ensure_ascii=False).lower()
    enforcement = str(props.get("enforcement", "")).lower()

    if enforcement == "stop" or '"stop"' in texto or "control de stop" in texto or "radar de stop" in texto:
        return "STOP"
    if any(p in texto for p in ("fin tramo", "final tramo", "fin de tramo", "final de tramo", "salida tramo")):
        return "TRAMO_FIN"
    if "tramo" in texto or "average_speed" in texto or "section" in texto:
        return "TRAMO_INICIO"
    if "semaforo" in texto or "semáforo" in texto or "traffic_signals" in texto or "foto-rojo" in texto:
        return "SEMAFORO"
    if "movil" in texto or "móvil" in texto:
        return "MOVIL"
    return "FIJO"


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
                lon = float(coords.__getitem__(0))
                lat = float(coords.__getitem__(1))
                radares.append({
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed": extraer_velocidad(props),
                    "type": clasificar_tipo(props),
                    "bearing": extraer_bearing(props),
                    "source": str(props.get("source", "ANUNCIADOS"))
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
                        "bearing": None,
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
            tipo_geom = geom.get("type")
            coords = geom.get("coordinates", [])
            tipo_radar = clasificar_tipo(props)
            velocidad = extraer_velocidad(props)
            rumbo = extraer_bearing(props)

            if tipo_geom == "Point" and len(coords) >= 2:
                radares.append({
                    "lat": round(float(coords.__getitem__(1)), 6),
                    "lon": round(float(coords.__getitem__(0)), 6),
                    "speed": velocidad,
                    "type": tipo_radar,
                    "bearing": rumbo,
                    "source": "OSM"
                })
    except Exception as e:
        print(f"Aviso: No se pudo leer el GeoJSON de OSM: {e}")
    return radares


def emparejar_tramos(radares):
    tramos = [r for r in radares if r.get("type") in ("TRAMO_INICIO", "TRAMO_FIN")]
    ya_emparejados = set()

    for i in range(len(tramos)):
        r1 = tramos.__getitem__(i)
        id1 = r1.get("id")
        if id1 in ya_emparejados:
            continue

        mejor_pareja = None
        mejor_dist = 35000.0

        for j in range(i + 1, len(tramos)):
            r2 = tramos.__getitem__(j)
            id2 = r2.get("id")
            if id2 in ya_emparejados:
                continue

            dist = haversine_metros(r1.get("lat"), r1.get("lon"), r2.get("lat"), r2.get("lon"))
            if 400.0 <= dist <= mejor_dist and r1.get("speed") == r2.get("speed"):
                mejor_dist = dist
                mejor_pareja = r2

        if mejor_pareja is not None:
            r1["type"] = "TRAMO_INICIO"
            mejor_pareja["type"] = "TRAMO_FIN"
            r1["pair_id"] = mejor_pareja.get("id")
            mejor_pareja["pair_id"] = r1.get("id")

            # Calculamos el rumbo exacto del tramo (de inicio a fin)
            rumbo_tramo = calcular_bearing(
                r1.get("lat"), r1.get("lon"),
                mejor_pareja.get("lat"), mejor_pareja.get("lon")
            )
            if r1.get("bearing") is None:
                r1["bearing"] = rumbo_tramo
            if mejor_pareja.get("bearing") is None:
                mejor_pareja["bearing"] = rumbo_tramo

            ya_emparejados.add(id1)
            ya_emparejados.add(mejor_pareja.get("id"))

    for r in tramos:
        if r.get("id") not in ya_emparejados:
            r["type"] = "FIJO"


def fusionar(listas_priorizadas):
    unificados = []
    for lista in listas_priorizadas:
        for candidato in lista:
            es_duplicado = False
            for existente in unificados:
                dist = haversine_metros(
                    existente.get("lat"), existente.get("lon"),
                    candidato.get("lat"), candidato.get("lon")
                )
                if dist <= DISTANCIA_DEDUPLICACION_METROS:
                    b_ex = existente.get("bearing")
                    b_ca = candidato.get("bearing")
                    # Si ambos tienen rumbo conocido y apuntan en sentidos opuestos (> 120°), NO fusionar
                    if b_ex is not None and b_ca is not None and diferencia_angular(b_ex, b_ca) > 120:
                        continue

                    es_duplicado = True
                    if existente.get("speed") is None and candidato.get("speed") is not None:
                        existente["speed"] = candidato.get("speed")
                    if existente.get("bearing") is None and candidato.get("bearing") is not None:
                        existente["bearing"] = candidato.get("bearing")
                    if existente.get("type") == "FIJO" and candidato.get("type") != "FIJO":
