"""
Motor de asignación inteligente tipo Yango/PedidosYa (§4.6 – Motor IA)

Scoring de taller para un incidente:
  score = 0.55 * dist_score + 0.30 * rating_score + 0.15 * disp_score + bonus_prioridad

- dist_score  : 1 - (distancia_km / RADIO_KM),  lineal inversa, 0 si > RADIO_KM
- rating_score: taller.rating / 5.0
- disp_score  : 1.0 si disponible, 0.3 si no
- bonus        : +0.12 para prioridad=alta (SOS), -0.05 para baja
"""
from math import radians, cos, sin, asin, sqrt
from typing import Optional
import json

# Especialidades disponibles en la plataforma
ESPECIALIDADES = [
    "mecanica_general",
    "electromecanica",
    "chaperia",
    "llanteria",
    "electricista",
    "pintura",
]

# Qué especialidades cubre cada tipo de incidente (IA)
ESPECIALIDADES_POR_TIPO: dict[str, list[str]] = {
    "bateria":  ["electricista", "electromecanica", "mecanica_general"],
    "llanta":   ["llanteria", "mecanica_general"],
    "motor":    ["mecanica_general", "electromecanica"],
    "choque":   ["chaperia", "mecanica_general"],
    "otros":    ["mecanica_general"],
}

_W_DIST = 0.55
_W_RATE = 0.30
_W_DISP = 0.15

RADIO_KM = 50.0  # Radio máximo de visibilidad (km)

_BONUS_PRIORIDAD = {"alta": 0.12, "media": 0.0, "baja": -0.05}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km entre dos puntos geográficos (fórmula Haversine)."""
    R = 6371.0
    la1, lo1, la2, lo2 = map(radians, [lat1, lon1, lat2, lon2])
    d_lat = la2 - la1
    d_lon = lo2 - lo1
    a = sin(d_lat / 2) ** 2 + cos(la1) * cos(la2) * sin(d_lon / 2) ** 2
    return 2.0 * R * asin(sqrt(max(0.0, min(1.0, a))))


def calcular_score(
    taller_lat: Optional[float],
    taller_lon: Optional[float],
    taller_rating: float,
    taller_disponible: bool,
    inc_lat: Optional[float],
    inc_lon: Optional[float],
    prioridad: str = "media",
) -> tuple[float, Optional[float]]:
    """Calcula el score (0-1) de un taller para un incidente.

    Returns:
        (score, distancia_km)  — distancia_km = None si no se puede calcular.
        score = 0 si fuera de radio o sin coordenadas (excepto SOS).
    """
    bonus = _BONUS_PRIORIDAD.get(prioridad, 0.0)
    rating_n  = min(1.0, (taller_rating or 0.0) / 5.0)
    disp_n    = 1.0 if taller_disponible else 0.3

    # SOS sin GPS del incidente: visible con score base (sin penalizar por distancia)
    if inc_lat is None or inc_lon is None:
        if prioridad == "alta":
            score = 0.55 * rating_n + 0.45 * disp_n + bonus
            return round(min(1.0, max(0.0, score)), 4), None
        return 0.0, None

    if taller_lat is None or taller_lon is None:
        return 0.0, None

    dist_km  = haversine(taller_lat, taller_lon, inc_lat, inc_lon)
    if dist_km > RADIO_KM:
        return 0.0, round(dist_km, 2)

    dist_n  = max(0.0, 1.0 - dist_km / RADIO_KM)
    score   = _W_DIST * dist_n + _W_RATE * rating_n + _W_DISP * disp_n + bonus
    return round(min(1.0, max(0.0, score)), 4), round(dist_km, 2)


def taller_tiene_especialidad(taller_especialidades_json: Optional[str], tipo_incidente: Optional[str]) -> bool:
    """Devuelve True si el taller tiene al menos una especialidad compatible con el tipo de incidente."""
    if not tipo_incidente:
        return True  # sin tipo conocido, cualquier taller puede atender
    requeridas = ESPECIALIDADES_POR_TIPO.get(tipo_incidente, ["mecanica_general"])
    if not taller_especialidades_json:
        return True  # taller sin especialidades declaradas puede atender cualquier cosa
    try:
        taller_esp = json.loads(taller_especialidades_json)
    except Exception:
        return True
    return any(e in requeridas for e in taller_esp)


def seleccionar_top3(
    talleres: list,   # lista de objetos Taller con .latitud, .longitud, .rating, .disponible, .especialidades
    inc_lat: Optional[float],
    inc_lon: Optional[float],
    prioridad: str = "media",
    tipo_incidente: Optional[str] = None,
    max_talleres: int = 3,
) -> list:
    """Selecciona hasta max_talleres talleres candidatos para invitar a un incidente.

    Filtra por especialidad primero; si no hay suficientes con la especialidad exacta,
    amplía al resto de talleres aprobados para garantizar que siempre haya candidatos.
    Devuelve la lista ordenada por score DESC.
    """
    aprobados = [t for t in talleres if t.estado == "aprobado"]

    # Primer intento: con filtro de especialidad
    candidatos = [
        t for t in aprobados
        if taller_tiene_especialidad(t.especialidades, tipo_incidente)
    ]

    # Fallback: si no hay suficientes con especialidad, usar todos los aprobados
    if len(candidatos) < max_talleres:
        candidatos = aprobados

    scored = []
    for t in candidatos:
        score, dist = calcular_score(
            t.latitud, t.longitud, t.rating or 0.0,
            t.disponible, inc_lat, inc_lon, prioridad,
        )
        scored.append((t, score, dist))

    # Si hay scores positivos, usar solo esos (hay GPS disponible)
    con_score = [(t, s, d) for t, s, d in scored if s > 0]
    if con_score:
        con_score.sort(key=lambda x: -x[1])
        return con_score[:max_talleres]

    # Sin GPS: ordenar por disponibilidad + rating y tomar los primeros
    scored.sort(key=lambda x: (-(1.0 if x[0].disponible else 0.3), -(x[0].rating or 0.0)))
    return [(t, 0.0, None) for t, _, _ in scored[:max_talleres]]
