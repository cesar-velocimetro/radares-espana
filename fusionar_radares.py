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


def haversine_metros(lat1, lon1, lat2, lon2):
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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
                    "source": str(props.get("source", "ANUNCIADOS")),
                    "_raw": json.dumps(props, ensure_ascii=False).lower()
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
            tipo_geom = geom.get("type")
            coords = geom.get("coordinates", [])
            tipo_radar = clasificar_tipo(props)
            velocidad = extraer_velocidad(props)

            if tipo_geom == "Point" and len(coords) >= 2:
                radares.append({
                    "lat": round(float(coords.__getitem__(1)), 6),
                    "lon": round(float(coords.__getitem__(0)), 6),
                    "speed": velocidad,
                    "type": tipo_radar,
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
            ya_emparejados.add(id1)
            ya_emparejados.add(mejor_pareja.get("id"))

    # Los puntos de tramo que no tienen pareja en la fuente pasan a FIJO para que nunca falte un TRAMO_FIN
    for r in tramos:
        if r.get("id") not in ya_emparejados:
            r["type"] = "FIJO"



def fusionar(listas_priorizadas):
    unificados = []
    for lista in listas_priorizadas:
        for candidato in lista:
            candidato.pop("_raw", None)
            es_duplicado = False
            for existente in unificados:
                dist = haversine_metros(
                    existente.get("lat"), existente.get("lon"),
                    candidato.get("lat"), candidato.get("lon")
                )
                if dist <= DISTANCIA_DEDUPLICACION_METROS:
                    es_duplicado = True
                    if existente.get("speed") is None and candidato.get("speed") is not None:
                        existente["speed"] = candidato.get("speed")
                    if existente.get("type") == "FIJO" and candidato.get("type") != "FIJO":
                        existente["type"] = candidato.get("type")
                    break

            if not es_duplicado:
                candidato["id"] = len(unificados) + 1
                unificados.append(candidato)

    emparejar_tramos(unificados)
    return unificados


if __name__ == "__main__":
    anunciados = cargar_radares_anunciados()
    dgt = cargar_nap_dgt()
    osm = cargar_osm_local()

    resultado = fusionar((anunciados, dgt, osm))

    conteo_tipos = {}
    for r in resultado:
        t = r.get("type")
        conteo_tipos[t] = conteo_tipos.get(t, 0) + 1

    print(f"Anunciados: {len(anunciados)} | DGT: {len(dgt)} | OSM (Iberia): {len(osm)}")
    print(f"Total únicos: {len(resultado)} -> Desglose por tipo: {conteo_tipos}")

    salida = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(resultado),
        "by_type": conteo_tipos,
        "radars": resultado
    }

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
