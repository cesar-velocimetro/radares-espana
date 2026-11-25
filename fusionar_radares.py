import json
import math
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

URL_RADARES_ANUNCIADOS = "https://geiserx.github.io/radares-anunciados-ha/feed.geojson"
URL_NAP_DGT = "https://nap.dgt.es/datex2/dgt/PredefinedLocationsPublication/radares/content.xml"
ARCHIVO_OSM_LOCAL = "radares_osm.geojson"
ARCHIVO_SALIDA = "radares_unificados.json"
DISTANCIA_DEDUPLICACION_METROS = 45.0

CARDINALES = {
    "n": 0, "norte": 0, "nne": 22, "ne": 45, "noreste": 45, "ene": 67,
    "e": 90, "este": 90, "ese": 112, "se": 135, "sureste": 135, "sse": 157,
    "s": 180, "sur": 180, "ssw": 202, "sso": 202, "sw": 225, "so": 225, "suroeste": 225,
    "wsw": 247, "oso": 247, "w": 270, "o": 270, "oeste": 270, "wnw": 292, "ono": 292,
    "nw": 315, "no": 315, "noroeste": 315, "nnw": 337, "nno": 337
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


def descargar_con_reintentos(url, reintentos=3):
    for intento in range(reintentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RadaresAppSync/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            if intento == reintentos - 1:
                raise e
            time.sleep(5 * (intento + 1))


def extraer_bearing(props):
    for clave in ("camera:direction", "direction", "bearing", "angle", "course", "heading", "sentido"):
        val = props.get(clave)
        if val is not None:
            texto = str(val).strip().lower()
            primero = texto.split(";")[0].strip()
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


def cargar_radares_anunciados():
    radares = []
    try:
        raw = descargar_con_reintentos(URL_RADARES_ANUNCIADOS)
        data = json.loads(raw.decode("utf-8"))
        for feat in data.get("features", []):
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})
            coords = geom.get("coordinates", [])
            if geom.get("type") == "Point" and len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
                radares.append({
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed": extraer_velocidad(props),
                    "type": clasificar_tipo(props),
                    "bearing": extraer_bearing(props),
                    "source": str(props.get("source", "ANUNCIADOS"))
                })
    except Exception as e:
        print(f"Aviso: No se pudo cargar Radares Anunciados: {e}", flush=True)
    return radares


def extraer_lat_lon_nodo(nodo):
    for elem in nodo.iter():
        if elem.tag.endswith("pointCoordinates"):
            lat, lon = None, None
            for child in elem:
                if child.tag.endswith("latitude"):
                    lat = float(child.text)
                elif child.tag.endswith("longitude"):
                    lon = float(child.text)
            if lat is not None and lon is not None:
                return round(lat, 6), round(lon, 6)
    return None, None


def cargar_nap_dgt():
    radares = []
    try:
        xml_bytes = descargar_con_reintentos(URL_NAP_DGT)
        root = ET.fromstring(xml_bytes)
        for loc in root.iter():
            if loc.tag.endswith("predefinedLocation") and loc.attrib.get("id"):
                nodo_from = next((c for c in loc.iter() if c.tag.endswith("from")), None)
                nodo_to = next((c for c in loc.iter() if c.tag.endswith("to")), None)
                if nodo_from is not None and nodo_to is not None:
                    lat_ini, lon_ini = extraer_lat_lon_nodo(nodo_from)
                    lat_fin, lon_fin = extraer_lat_lon_nodo(nodo_to)
                    if lat_ini is not None and lat_fin is not None:
                        rumbo = calcular_bearing(lat_ini, lon_ini, lat_fin, lon_fin)
                        clave = loc.attrib.get("id")
                        radares.append({
                            "lat": lat_ini, "lon": lon_ini, "speed": None,
                            "type": "TRAMO_INICIO", "bearing": rumbo,
                            "source": "DGT_NAP", "_dgt_pair_key": clave
                        })
                        radares.append({
                            "lat": lat_fin, "lon": lon_fin, "speed": None,
                            "type": "TRAMO_FIN", "bearing": rumbo,
                            "source": "DGT_NAP", "_dgt_pair_key": clave
                        })
                else:
                    lat, lon = extraer_lat_lon_nodo(loc)
                    if lat is not None and lon is not None:
                        radares.append({
                            "lat": lat, "lon": lon, "speed": None,
                            "type": "FIJO", "bearing": None, "source": "DGT_NAP"
                        })
    except Exception as e:
        print(f"Aviso: No se pudo cargar NAP DGT: {e}", flush=True)
    return radares


def cargar_osm_local():
    radares = []
    try:
        with open(ARCHIVO_OSM_LOCAL, "r", encoding="utf-8") as f:
            data = json.load(f)
        for feat in data.get("features", []):
            geom, props = feat.get("geometry", {}), feat.get("properties", {})
            coords = geom.get("coordinates", [])
            if geom.get("type") == "Point" and len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
                radares.append({
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed": extraer_velocidad(props),
                    "type": clasificar_tipo(props),
                    "bearing": extraer_bearing(props),
                    "source": "OSM"
                })
    except Exception as e:
        print(f"Aviso: No se pudo leer el GeoJSON de OSM: {e}", flush=True)
    return radares


def emparejar_tramos(radares):
    ya_emparejados, por_clave_dgt = set(), {}
    for r in radares:
        clave = r.pop("_dgt_pair_key", None)
        if clave:
            por_clave_dgt.setdefault(clave, []).append(r)

    for grupo in por_clave_dgt.values():
        if len(grupo) == 2:
            r_ini, r_fin = grupo[0], grupo[1]
            r_ini["type"], r_fin["type"] = "TRAMO_INICIO", "TRAMO_FIN"
            r_ini["pair_id"], r_fin["pair_id"] = r_fin.get("id"), r_ini.get("id")
            ya_emparejados.update((r_ini.get("id"), r_fin.get("id")))

    tramos = [r for r in radares if r.get("type") in ("TRAMO_INICIO", "TRAMO_FIN") and r.get("id") not in ya_emparejados]
    for i, r1 in enumerate(tramos):
        if r1.get("id") not in ya_emparejados:
            mejor_pareja, mejor_dist = None, 35000.0
            for r2 in tramos[i + 1:]:
                if r2.get("id") not in ya_emparejados:
                    dist = haversine_metros(r1.get("lat"), r1.get("lon"), r2.get("lat"), r2.get("lon"))
                    if 400.0 <= dist <= mejor_dist and r1.get("speed") == r2.get("speed"):
                        mejor_dist, mejor_pareja = dist, r2
            if mejor_pareja is not None:
                r1["type"], mejor_pareja["type"] = "TRAMO_INICIO", "TRAMO_FIN"
                r1["pair_id"], mejor_pareja["pair_id"] = mejor_pareja.get("id"), r1.get("id")
                rumbo = calcular_bearing(r1.get("lat"), r1.get("lon"), mejor_pareja.get("lat"), mejor_pareja.get("lon"))
                if r1.get("bearing") is None:
                    r1["bearing"] = rumbo
                if mejor_pareja.get("bearing") is None:
                    mejor_pareja["bearing"] = rumbo
                ya_emparejados.update((r1.get("id"), mejor_pareja.get("id")))

    for r in radares:
        if r.get("type") in ("TRAMO_INICIO", "TRAMO_FIN") and r.get("id") not in ya_emparejados:
            r["type"] = "FIJO"


def fusionar(listas_priorizadas):
    unificados = []
    for lista in listas_priorizadas:
        for candidato in lista:
            es_duplicado = False
            for existente in unificados:
                if haversine_metros(existente.get("lat"), existente.get("lon"), candidato.get("lat"), candidato.get("lon")) <= DISTANCIA_DEDUPLICACION_METROS:
                    b_ex, b_ca = existente.get("bearing"), candidato.get("bearing")
                    if b_ex is not None and b_ca is not None and diferencia_angular(b_ex, b_ca) > 120:
                        continue
                    es_duplicado = True
                    if existente.get("speed") is None and candidato.get("speed") is not None:
                        existente["speed"] = candidato.get("speed")
                    if existente.get("bearing") is None and candidato.get("bearing") is not None:
                        existente["bearing"] = candidato.get("bearing")
                    if existente.get("type") == "FIJO" and candidato.get("type") != "FIJO":
                        existente["type"] = candidato.get("type")
                    if "_dgt_pair_key" in candidato and "_dgt_pair_key" not in existente:
                        existente["_dgt_pair_key"] = candidato.get("_dgt_pair_key")
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

    conteo_tipos, con_bearing = {}, 0
    for r in resultado:
        t = r.get("type")
        conteo_tipos[t] = conteo_tipos.get(t, 0) + 1
        if r.get("bearing") is not None:
            con_bearing += 1

    print(f"Anunciados: {len(anunciados)} | DGT: {len(dgt)} | OSM (Iberia): {len(osm)}", flush=True)
    print(f"Total únicos: {len(resultado)} (Con orientación: {con_bearing}) -> Desglose: {conteo_tipos}", flush=True)

    salida = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(resultado),
        "with_bearing": con_bearing,
        "by_type": conteo_tipos,
        "radars": resultado
    }

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
