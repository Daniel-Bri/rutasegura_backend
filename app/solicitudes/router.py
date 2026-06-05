import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.dependencies import require_role, get_current_user
from app.acceso_registro.models import User
from app.emergencias.models import Incidente, Evidencia
from app.talleres_tecnicos.models import Asignacion
from app.talleres_tecnicos.schemas import AsignacionResponse
from app.talleres_tecnicos.service import get_taller_by_user
from app.ia.motor_asignacion import calcular_score, haversine
from app.ia import clasificador

router = APIRouter()

_ESTADOS_CERRADOS = ["cancelado", "finalizado"]


class SolicitudDisponibleResponse(BaseModel):
    incidente_id: int
    latitud: Optional[float]
    longitud: Optional[float]
    descripcion: Optional[str]
    tipo_problema: str
    prioridad: str
    estado: str
    fotos_urls: list[str]
    tiene_audio: bool
    created_at: str
    es_sos: bool = False
    distancia_km: Optional[float] = None
    score_ia: float = 0.0


class AceptarPayload(BaseModel):
    eta: Optional[int] = None


# ── CU18 – Asignaciones activas del cliente (para chat) ──────────────────
@router.get("/mis-asignaciones", response_model=list[AsignacionResponse])
async def mis_asignaciones_cliente(
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Asignacion)
        .join(Incidente, Asignacion.incidente_id == Incidente.id)
        .where(
            Incidente.usuario_id == current_user.id,
            Asignacion.estado.notin_(_ESTADOS_CERRADOS),
        )
        .order_by(Asignacion.created_at.desc())
    )
    return [AsignacionResponse.model_validate(a) for a in result.scalars().all()]


# ── CU13 – Ver solicitudes del taller (invitadas + activas) ─────────────
@router.get("/disponibles", response_model=list[SolicitudDisponibleResponse])
async def disponibles(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve las asignaciones que el taller tiene en estado invitado o activo,
    con los datos del incidente asociado para que el taller pueda cotizar."""
    taller = await get_taller_by_user(current_user.id, db)

    # Asignaciones del taller que están en estados activos
    asig_res = await db.execute(
        select(Asignacion)
        .where(
            Asignacion.taller_id == taller.id,
            Asignacion.estado.in_(["invitado", "aceptado", "en_camino", "en_sitio", "en_reparacion"]),
        )
        .order_by(Asignacion.created_at.desc())
    )
    asignaciones = list(asig_res.scalars().all())

    if not asignaciones:
        return []

    inc_ids = [a.incidente_id for a in asignaciones]
    inc_res = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id.in_(inc_ids))
    )
    incidentes_map = {i.id: i for i in inc_res.scalars().all()}

    evid_res = await db.execute(
        select(Evidencia.incidente_id, Evidencia.url, Evidencia.tipo)
        .where(Evidencia.incidente_id.in_(inc_ids))
    )
    fotos_map: dict[int, list[str]] = {}
    audio_map: dict[int, bool] = {}
    for row in evid_res.all():
        if row[2] == "foto" and row[1]:
            fotos_map.setdefault(row[0], []).append(row[1])
        elif row[2] == "audio":
            audio_map[row[0]] = True

    resultado: list[SolicitudDisponibleResponse] = []
    for asig in asignaciones:
        i = incidentes_map.get(asig.incidente_id)
        if not i:
            continue
        _, distancia = calcular_score(
            taller.latitud, taller.longitud, taller.rating or 0.0,
            taller.disponible, i.latitud, i.longitud, i.prioridad,
        )
        tipo_problema = i.tipo_incidente or ""
        if not tipo_problema and i.descripcion:
            tipo_problema = clasificador.clasificar(i.descripcion).get("etiqueta_es", "")

        resultado.append(SolicitudDisponibleResponse(
            incidente_id=i.id,
            latitud=float(i.latitud) if i.latitud is not None else None,
            longitud=float(i.longitud) if i.longitud is not None else None,
            descripcion=i.descripcion,
            tipo_problema=tipo_problema,
            prioridad=i.prioridad,
            estado=asig.estado,   # estado de la ASIGNACIÓN, no del incidente
            fotos_urls=fotos_map.get(i.id, []),
            tiene_audio=audio_map.get(i.id, False),
            created_at=i.created_at.isoformat() if i.created_at else "",
            es_sos=(i.prioridad == "alta" and "SOS" in (i.descripcion or "")),
            distancia_km=distancia,
            score_ia=0.0,
        ))

    return resultado


# ── CU15 – Aceptar solicitud ──────────────────────────────────────────────
@router.patch("/{incidente_id}/aceptar", response_model=AsignacionResponse)
async def aceptar(
    incidente_id: int,
    body: AceptarPayload,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await get_taller_by_user(current_user.id, db)

    tiene_asig = (
        exists()
        .where(
            and_(
                Asignacion.incidente_id == incidente_id,
                Asignacion.estado.notin_(_ESTADOS_CERRADOS),
            )
        )
    )
    row = await db.execute(
        select(Incidente, tiene_asig.correlate(None))
        .where(Incidente.id == incidente_id)
    )
    fila = row.first()

    if fila is None:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    incidente, ya_asignado = fila
    if incidente.estado != "pendiente":
        raise HTTPException(status_code=400, detail="El incidente ya no está disponible")
    if ya_asignado:
        raise HTTPException(status_code=400, detail="El incidente ya tiene un taller asignado")

    # §4.6 – ETA automático si no se provee y hay coordenadas en ambos extremos
    eta_final = body.eta
    if eta_final is None and taller.latitud and taller.longitud and incidente.latitud and incidente.longitud:
        dist_km = haversine(taller.latitud, taller.longitud, incidente.latitud, incidente.longitud)
        eta_final = max(5, math.ceil(dist_km / 30 * 60))  # 30 km/h urbano, mínimo 5 min

    ahora = datetime.now(timezone.utc)
    asignacion = Asignacion(
        incidente_id=incidente_id,
        taller_id=taller.id,
        eta=eta_final,
        estado="aceptado",
    )
    db.add(asignacion)
    incidente.estado = "en_proceso"
    await db.flush()
    await db.commit()

    try:
        from app.comunicacion.models import Notificacion
        from app.comunicacion.websocket import notify
        from app.notificaciones.service import notificar_usuario
        eta_txt = f"{eta_final} minutos" if eta_final else "por determinar"
        notif_titulo = "Solicitud aceptada"
        notif_msg = f"Un taller aceptó tu emergencia. Tiempo estimado de llegada: {eta_txt}."
        db.add(Notificacion(
            usuario_id=incidente.usuario_id,
            titulo=notif_titulo,
            mensaje=notif_msg,
            tipo="asignacion",
            referencia_id=incidente_id,
        ))
        await db.commit()
        await notify(incidente.usuario_id, "notificacion", {
            "titulo": notif_titulo, "mensaje": notif_msg,
            "tipo": "asignacion", "referencia_id": incidente_id,
        })
        await notificar_usuario(
            incidente.usuario_id, notif_titulo, notif_msg, db,
            {"tipo": "asignacion", "incidente_id": str(incidente_id)},
        )
    except Exception:
        pass

    return AsignacionResponse(
        id=asignacion.id,
        incidente_id=asignacion.incidente_id,
        taller_id=asignacion.taller_id,
        tecnico_id=asignacion.tecnico_id,
        estado=asignacion.estado,
        eta=asignacion.eta,
        observacion=asignacion.observacion,
        created_at=ahora,
    )


# ── CU14 – Ver detalle del incidente ─────────────────────────────────────
@router.get("/{solicitud_id}")
async def detalle(
    solicitud_id: int,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == solicitud_id)
    )
    incidente = result.scalar_one_or_none()
    if not incidente:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")
    return {
        "id":             incidente.id,
        "latitud":        incidente.latitud,
        "longitud":       incidente.longitud,
        "descripcion":    incidente.descripcion,
        "tipo_incidente": incidente.tipo_incidente,
        "estado":         incidente.estado,
        "prioridad":      incidente.prioridad,
        "created_at":     incidente.created_at.isoformat() if incidente.created_at else None,
    }


# ── CU10 – Ver estado de solicitud ───────────────────────────────────────
@router.get("/{solicitud_id}/estado")
async def ver_estado(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == solicitud_id)
    )
    incidente = result.scalar_one_or_none()
    if not incidente:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    if current_user.role == "cliente" and incidente.usuario_id != current_user.id:
        raise HTTPException(status_code=403, detail="No tienes acceso a este incidente")

    asig_result = await db.execute(
        select(Asignacion)
        .where(
            Asignacion.incidente_id == solicitud_id,
            Asignacion.estado.notin_(["cancelado"]),
        )
        .order_by(Asignacion.created_at.desc())
    )
    asignacion = asig_result.scalar_one_or_none()

    taller_info = None
    if asignacion:
        from app.acceso_registro.models import Taller
        tal_res = await db.execute(select(Taller).where(Taller.id == asignacion.taller_id))
        taller = tal_res.scalar_one_or_none()
        if taller:
            taller_info = {
                "id": taller.id,
                "nombre": taller.nombre,
                "telefono": taller.telefono,
                "direccion": taller.direccion,
            }

    return {
        "incidente_id": incidente.id,
        "estado_incidente": incidente.estado,
        "prioridad": incidente.prioridad,
        "tipo_incidente": incidente.tipo_incidente,
        "asignacion": {
            "id": asignacion.id,
            "estado": asignacion.estado,
            "eta": asignacion.eta,
            "tecnico_id": asignacion.tecnico_id,
        } if asignacion else None,
        "taller": taller_info,
    }


# ── CU11 – Cancelar solicitud (cliente) ──────────────────────────────────
@router.patch("/{solicitud_id}/cancelar")
async def cancelar(
    solicitud_id: int,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incidente).where(
            Incidente.id == solicitud_id,
            Incidente.usuario_id == current_user.id,
        )
    )
    incidente = result.scalar_one_or_none()
    if not incidente:
        raise HTTPException(status_code=404, detail="Incidente no encontrado o no te pertenece")
    if incidente.estado in ("resuelto", "cancelado"):
        raise HTTPException(status_code=400, detail=f"El incidente ya está {incidente.estado}")

    # Buscar asignación activa
    asig_res = await db.execute(
        select(Asignacion).where(
            Asignacion.incidente_id == solicitud_id,
            Asignacion.estado.notin_(_ESTADOS_CERRADOS),
        )
    )
    asignaciones_activas = list(asig_res.scalars().all())

    # Bloquear cancelación si el vehículo ya está en reparación
    en_reparacion = any(a.estado == "en_reparacion" for a in asignaciones_activas)
    if en_reparacion:
        raise HTTPException(
            status_code=400,
            detail="No puedes cancelar: el vehículo ya está en reparación. Contacta al taller directamente."
        )

    # Verificar si el técnico ya llegó (en_sitio) → habrá cobro por visita
    tecnico_en_sitio = any(a.estado == "en_sitio" for a in asignaciones_activas)

    incidente.estado = "cancelado"
    for asig in asignaciones_activas:
        asig.estado = "cancelado"

    await db.commit()

    # Notificar a los talleres
    try:
        from app.notificaciones.service import notificar_usuario
        from app.acceso_registro.models import Taller as _Taller
        for asig in asignaciones_activas:
            t_res = await db.execute(select(_Taller.usuario_id).where(_Taller.id == asig.taller_id))
            t_row = t_res.first()
            if t_row:
                msg = (
                    f"El cliente canceló el incidente #{solicitud_id}. "
                    + ("El técnico ya estaba en sitio — puedes registrar un cobro por visita." if tecnico_en_sitio else "")
                )
                await notificar_usuario(
                    t_row[0], "❌ Solicitud cancelada", msg, db,
                    {"tipo": "solicitud_cancelada", "incidente_id": str(solicitud_id),
                     "cobro_visita_posible": str(tecnico_en_sitio)},
                )
    except Exception:
        pass

    return {
        "id": incidente.id,
        "estado": incidente.estado,
        "msg": "Solicitud cancelada correctamente",
        "cobro_visita_posible": tecnico_en_sitio,
    }


# ── CU16 – Rechazar asignación (taller) ──────────────────────────────────
@router.patch("/{solicitud_id}/rechazar")
async def rechazar(
    solicitud_id: int,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await get_taller_by_user(current_user.id, db)

    result = await db.execute(
        select(Asignacion).where(
            Asignacion.incidente_id == solicitud_id,
            Asignacion.taller_id == taller.id,
            Asignacion.estado.notin_(_ESTADOS_CERRADOS),
        )
    )
    asignacion = result.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="No tienes una asignación activa para este incidente")

    if asignacion.estado != "aceptado":
        raise HTTPException(
            status_code=400,
            detail=f"Solo puedes rechazar en estado 'aceptado', estado actual: '{asignacion.estado}'",
        )

    asignacion.estado = "cancelado"

    # Devolver incidente a pendiente para que otro taller pueda aceptarlo
    inc_res = await db.execute(select(Incidente).where(Incidente.id == solicitud_id))
    incidente = inc_res.scalar_one_or_none()
    if incidente and incidente.estado == "en_proceso":
        incidente.estado = "pendiente"

    await db.commit()

    try:
        from app.comunicacion.models import Notificacion
        from app.comunicacion.websocket import notify
        from app.notificaciones.service import notificar_usuario
        if incidente:
            notif_titulo = "Taller rechazó tu solicitud"
            notif_msg = "Un taller rechazó tu emergencia. El sistema buscará otro taller disponible."
            db.add(Notificacion(
                usuario_id=incidente.usuario_id,
                titulo=notif_titulo,
                mensaje=notif_msg,
                tipo="asignacion",
                referencia_id=solicitud_id,
            ))
            await db.commit()
            await notify(incidente.usuario_id, "notificacion", {
                "titulo": notif_titulo, "mensaje": notif_msg,
                "tipo": "asignacion", "referencia_id": solicitud_id,
            })
            await notificar_usuario(
                incidente.usuario_id, notif_titulo, notif_msg, db,
                {"tipo": "asignacion", "incidente_id": str(solicitud_id)},
            )
    except Exception:
        pass

    return {
        "asignacion_id": asignacion.id,
        "incidente_id":  solicitud_id,
        "estado":        "cancelado",
        "msg":           "Asignación rechazada. El incidente vuelve a estar disponible para otros talleres.",
    }
