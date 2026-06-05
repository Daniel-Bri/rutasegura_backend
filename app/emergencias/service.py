import json
import logging
import os
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.emergencias.models import Evidencia, Incidente
from app.emergencias.schemas import IncidenteCreate, UbicacionUpdate
from app.acceso_registro.models import Taller, Vehiculo
from app.talleres_tecnicos.models import Asignacion
from app.ia import clasificador

logger = logging.getLogger(__name__)

_UPLOAD_DIR = "uploads"


# ── helpers ─────────────────────────────────────────────────────────────────

async def _refetch(incidente_id: int, db: AsyncSession) -> "Incidente":
    """Re-fetch with tipo_incidente undeferred to avoid MissingGreenlet on lazy load."""
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == incidente_id)
    )
    return result.scalar_one()


async def _get_incidente_usuario(
    incidente_id: int, usuario_id: int, db: AsyncSession
) -> Incidente:
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == incidente_id, Incidente.usuario_id == usuario_id)
    )
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")
    return inc


# ── CU05 ─────────────────────────────────────────────────────────────────────

async def crear_incidente(
    data: IncidenteCreate, usuario_id: int, db: AsyncSession
) -> Incidente:
    result = await db.execute(
        select(Vehiculo).where(
            Vehiculo.id == data.vehiculo_id,
            Vehiculo.usuario_id == usuario_id,
            Vehiculo.activo.is_(True),
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Vehículo no encontrado o no pertenece al usuario")

    # §4.5 – Clasificación automática con IA
    tipo_incidente = None
    if data.descripcion and data.descripcion.strip():
        res_ia = clasificador.clasificar(data.descripcion)
        tipo_incidente = res_ia["tipo"]

    incidente = Incidente(
        usuario_id=usuario_id,
        vehiculo_id=data.vehiculo_id,
        descripcion=data.descripcion,
        prioridad=data.prioridad or "media",
        tipo_incidente=tipo_incidente,
    )
    db.add(incidente)
    await db.commit()

    # Re-fetch antes de invitar para evitar atributos expirados post-commit
    incidente_refetched = await _refetch(incidente.id, db)

    try:
        await _invitar_talleres(incidente_refetched, db)
    except Exception as e:
        logger.warning(f"[invitar_talleres] Fallo en creación: {e}")

    return await _refetch(incidente.id, db)


async def _invitar_talleres(incidente: Incidente, db: AsyncSession) -> None:
    """Busca los 3 mejores talleres y crea asignaciones en estado 'invitado'."""
    from app.ia.motor_asignacion import seleccionar_top3
    from app.talleres_tecnicos.models import Asignacion

    talleres_res = await db.execute(select(Taller).where(Taller.estado == "aprobado"))
    talleres = list(talleres_res.scalars().all())

    if not talleres:
        logger.warning(f"[invitar_talleres] Incidente #{incidente.id}: no hay talleres aprobados en el sistema")
        return

    seleccionados = seleccionar_top3(
        talleres,
        inc_lat=float(incidente.latitud) if incidente.latitud else None,
        inc_lon=float(incidente.longitud) if incidente.longitud else None,
        prioridad=incidente.prioridad or "media",
        tipo_incidente=incidente.tipo_incidente,
    )

    if not seleccionados:
        logger.warning(f"[invitar_talleres] Incidente #{incidente.id}: motor no encontró talleres candidatos")
        return

    for taller, score, dist in seleccionados:
        from math import ceil
        from app.ia.motor_asignacion import haversine
        eta = None
        if taller.latitud and taller.longitud and incidente.latitud and incidente.longitud:
            dist_km = haversine(taller.latitud, taller.longitud,
                                float(incidente.latitud), float(incidente.longitud))
            eta = max(5, ceil(dist_km / 30 * 60))

        asig = Asignacion(
            incidente_id=incidente.id,
            taller_id=taller.id,
            estado="invitado",
            eta=eta,
        )
        db.add(asig)

    incidente.estado = "en_proceso"
    await db.commit()

    # Notificar a los talleres invitados
    try:
        from app.comunicacion.websocket import notify
        from app.notificaciones.service import notificar_usuario
        for taller, _, _ in seleccionados:
            await notificar_usuario(
                taller.usuario_id,
                "🚨 Nueva solicitud de emergencia",
                f"Tienes una nueva solicitud de emergencia asignada. Envía tu cotización para aceptarla.",
                db,
                {"tipo": "nueva_solicitud_invitado", "incidente_id": str(incidente.id)},
            )
            await notify(taller.usuario_id, "notificacion", {
                "titulo": "🚨 Nueva solicitud",
                "mensaje": "Tienes una nueva solicitud de emergencia. Envía tu cotización.",
                "tipo": "nueva_solicitud_invitado",
                "referencia_id": incidente.id,
            })
    except Exception:
        pass


# ── CU30 ─────────────────────────────────────────────────────────────────────

async def crear_incidente_sos(
    usuario_id: int,
    latitud: float | None,
    longitud: float | None,
    db: AsyncSession,
) -> Incidente:
    veh_res = await db.execute(
        select(Vehiculo)
        .where(Vehiculo.usuario_id == usuario_id, Vehiculo.activo.is_(True))
        .order_by(Vehiculo.created_at.asc())
    )
    vehiculo = veh_res.scalars().first()
    if not vehiculo:
        raise HTTPException(
            status_code=400,
            detail="Debes tener al menos un vehículo registrado para usar el botón SOS",
        )

    incidente = Incidente(
        usuario_id=usuario_id,
        vehiculo_id=vehiculo.id,
        descripcion="🆘 Alerta SOS — Emergencia urgente enviada desde la app",
        prioridad="alta",
        latitud=latitud,
        longitud=longitud,
        tipo_incidente="otros",
    )
    db.add(incidente)
    await db.commit()

    incidente_refetched = await _refetch(incidente.id, db)
    try:
        await _invitar_talleres(incidente_refetched, db)
    except Exception as e:
        logger.warning(f"[invitar_talleres SOS] Fallo: {e}")

    return await _refetch(incidente.id, db)


# ── CU06 ─────────────────────────────────────────────────────────────────────

async def actualizar_ubicacion(
    incidente_id: int, usuario_id: int, data: UbicacionUpdate, db: AsyncSession
) -> Incidente:
    from app.talleres_tecnicos.models import Asignacion
    incidente = await _get_incidente_usuario(incidente_id, usuario_id, db)
    incidente.latitud  = data.latitud
    incidente.longitud = data.longitud
    await db.commit()

    # Re-intentar invitación ahora que tenemos GPS (si aún no hay talleres invitados)
    try:
        asig_res = await db.execute(
            select(Asignacion).where(
                Asignacion.incidente_id == incidente_id,
                Asignacion.estado == "invitado",
            )
        )
        ya_invitados = list(asig_res.scalars().all())
        if not ya_invitados:
            inc = await _refetch(incidente_id, db)
            await _invitar_talleres(inc, db)
    except Exception as e:
        logger.warning(f"[actualizar_ubicacion] Re-invitación fallida: {e}")

    return await _refetch(incidente.id, db)


# ── CU09 ─────────────────────────────────────────────────────────────────────

async def actualizar_descripcion(
    incidente_id: int, usuario_id: int, descripcion: str, db: AsyncSession
) -> Incidente:
    """Actualiza la descripción y re-clasifica el tipo con IA."""
    incidente = await _get_incidente_usuario(incidente_id, usuario_id, db)
    incidente.descripcion = descripcion
    if descripcion.strip():
        res_ia = clasificador.clasificar(descripcion)
        incidente.tipo_incidente = res_ia["tipo"]
    await db.commit()
    return await _refetch(incidente.id, db)


# ── CU07 ─────────────────────────────────────────────────────────────────────

async def guardar_foto(
    incidente_id: int,
    usuario_id: int,
    imagen_bytes: bytes,
    filename: str,
    db: AsyncSession,
) -> dict:
    """Guarda la foto, ejecuta análisis IA (§4.4 + §4.5)."""
    await _get_incidente_usuario(incidente_id, usuario_id, db)

    from app.ia import analizador_imagen

    ts  = int(time.time() * 1000)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    ruta_rel = f"fotos/{incidente_id}_{ts}.{ext}"
    ruta_abs = os.path.join(_UPLOAD_DIR, ruta_rel)
    os.makedirs(os.path.dirname(ruta_abs), exist_ok=True)

    with open(ruta_abs, "wb") as fh:
        fh.write(imagen_bytes)

    analisis   = analizador_imagen.analizar(imagen_bytes)
    url_publica = f"/uploads/{ruta_rel}"

    evidencia = Evidencia(
        incidente_id=incidente_id,
        tipo="foto",
        ruta=ruta_abs,
        url=url_publica,
        analisis_ia=json.dumps(analisis),
    )
    db.add(evidencia)
    await db.commit()
    await db.refresh(evidencia)

    return {
        "evidencia_id": evidencia.id,
        "url": url_publica,
        "analisis_ia": analisis,
    }


# ── CU08 ─────────────────────────────────────────────────────────────────────

async def guardar_audio(
    incidente_id: int,
    usuario_id: int,
    audio_bytes: bytes,
    filename: str,
    db: AsyncSession,
) -> dict:
    """Guarda el audio, transcribe y clasifica el incidente con IA (§4.5)."""
    await _get_incidente_usuario(incidente_id, usuario_id, db)

    from app.ia import transcriptor, clasificador as clf

    ts  = int(time.time() * 1000)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
    if ext not in ("wav", "mp3", "ogg", "m4a", "flac"):
        ext = "wav"
    ruta_rel = f"audio/{incidente_id}_{ts}.{ext}"
    ruta_abs = os.path.join(_UPLOAD_DIR, ruta_rel)
    os.makedirs(os.path.dirname(ruta_abs), exist_ok=True)

    with open(ruta_abs, "wb") as fh:
        fh.write(audio_bytes)

    res_transcripcion = transcriptor.transcribir(audio_bytes, ext)
    clasificacion     = None
    texto_transcrito  = res_transcripcion.get("transcripcion", "")

    if res_transcripcion.get("exito") and texto_transcrito:
        clasificacion = clf.clasificar(texto_transcrito)
        # Actualizar tipo_incidente si la confianza es alta y aún no tenía tipo
        if clasificacion and clasificacion.get("confianza", 0) > 0.5:
            inc_upd = await db.execute(
                select(Incidente)
                .options(undefer(Incidente.tipo_incidente))
                .where(Incidente.id == incidente_id)
            )
            inc = inc_upd.scalar_one_or_none()
            if inc and not inc.tipo_incidente:
                inc.tipo_incidente = clasificacion["tipo"]

    url_publica = f"/uploads/{ruta_rel}"
    evidencia = Evidencia(
        incidente_id=incidente_id,
        tipo="audio",
        ruta=ruta_abs,
        url=url_publica,
        transcripcion=texto_transcrito or None,
        analisis_ia=json.dumps(clasificacion) if clasificacion else None,
    )
    db.add(evidencia)
    await db.commit()
    await db.refresh(evidencia)

    return {
        "evidencia_id": evidencia.id,
        "url": url_publica,
        "transcripcion": res_transcripcion,
        "clasificacion": clasificacion,
    }


# ── CU10 ─────────────────────────────────────────────────────────────────────

async def listar_incidentes_usuario(usuario_id: int, db: AsyncSession) -> list[Incidente]:
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.usuario_id == usuario_id)
        .order_by(Incidente.created_at.desc())
    )
    return list(result.scalars().all())


async def obtener_incidente(incidente_id: int, db: AsyncSession) -> Incidente:
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == incidente_id)
    )
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")
    return inc


async def listar_mis_solicitudes(usuario_id: int, db: AsyncSession) -> list[dict]:
    inc_res = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.usuario_id == usuario_id)
        .order_by(Incidente.created_at.desc())
    )
    incidentes = list(inc_res.scalars().all())

    rows = []
    for inc in incidentes:
        asig_res = await db.execute(
            select(Asignacion)
            .where(Asignacion.incidente_id == inc.id)
            .order_by(Asignacion.created_at.desc())
        )
        # Un incidente puede tener varias asignaciones (rechazadas + activa).
        # Tomamos la más reciente no-cancelada; si todas están canceladas, la más reciente.
        all_asigs = list(asig_res.scalars().all())
        asig = next((a for a in all_asigs if a.estado != "cancelado"), None) or (all_asigs[0] if all_asigs else None)

        asig_data = None
        if asig:
            taller_res = await db.execute(select(Taller).where(Taller.id == asig.taller_id))
            taller     = taller_res.scalar_one_or_none()
            asig_data  = {
                "id": asig.id,
                "estado": asig.estado,
                "eta": asig.eta,
                "taller_id": asig.taller_id,
                "taller_nombre": taller.nombre if taller else None,
                "tecnico_id": asig.tecnico_id,
                "observacion": asig.observacion,
            }

        # Evidencias reales (§4.4)
        evid_res = await db.execute(
            select(Evidencia.tipo, Evidencia.url)
            .where(Evidencia.incidente_id == inc.id)
        )
        evid_rows = evid_res.all()
        fotos_urls = [r.url for r in evid_rows if r.tipo == "foto" and r.url]

        rows.append({
            "incidente": {
                "id":            inc.id,
                "vehiculo_id":   inc.vehiculo_id,
                "estado":        inc.estado,
                "prioridad":     inc.prioridad,
                "tipo_incidente": inc.tipo_incidente,
                "descripcion":   inc.descripcion,
                "latitud":       inc.latitud,
                "longitud":      inc.longitud,
                "created_at":    inc.created_at.isoformat() if inc.created_at else None,
            },
            "asignacion": asig_data,
            "fotos_urls": fotos_urls,
        })
    return rows
