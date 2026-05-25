from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.acceso_registro.models import Taller, User
from app.comunicacion.models import Mensaje
from app.comunicacion.schemas import (
    MensajeCreate, MensajeResponse,
    UbicacionTecnicoResponse, UbicacionTecnicoUpdate,
)
from app.emergencias.models import Incidente
from app.talleres_tecnicos.models import Asignacion, Tecnico


# ── CU17 · Ubicación ──────────────────────────────────────────

async def actualizar_ubicacion_tecnico(
    user_id: int, data: UbicacionTecnicoUpdate, db: AsyncSession
) -> dict:
    result = await db.execute(
        select(Tecnico).where(Tecnico.usuario_id == user_id, Tecnico.activo.is_(True))
    )
    tecnico = result.scalar_one_or_none()
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    tecnico.latitud = data.latitud
    tecnico.longitud = data.longitud
    tecnico.ultima_actualizacion = datetime.now(timezone.utc)
    await db.commit()

    # CU37 — Broadcast ubicación a clientes de asignaciones activas
    try:
        from app.comunicacion.websocket import notify
        estados_activos = ("aceptado", "en_camino", "en_sitio", "en_reparacion")
        asig_res = await db.execute(
            select(Asignacion.id, Asignacion.incidente_id)
            .where(Asignacion.tecnico_id == tecnico.id, Asignacion.estado.in_(estados_activos))
        )
        for asig_id, inc_id in asig_res.all():
            inc_res = await db.execute(select(Incidente.usuario_id).where(Incidente.id == inc_id))
            cliente_id = inc_res.scalar_one_or_none()
            if cliente_id:
                await notify(cliente_id, "ubicacion_tecnico", {
                    "asignacion_id": asig_id,
                    "tecnico_id": tecnico.id,
                    "nombre": tecnico.nombre,
                    "latitud": data.latitud,
                    "longitud": data.longitud,
                })
    except Exception:
        pass

    return {"ok": True}


async def obtener_ubicacion_tecnico(
    asignacion_id: int, usuario_id: int, db: AsyncSession
) -> UbicacionTecnicoResponse:
    result = await db.execute(
        select(Asignacion).where(Asignacion.id == asignacion_id)
    )
    asignacion = result.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    res_inc = await db.execute(
        select(Incidente).where(
            Incidente.id == asignacion.incidente_id,
            Incidente.usuario_id == usuario_id,
        )
    )
    if not res_inc.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta asignación")

    if not asignacion.tecnico_id:
        raise HTTPException(status_code=404, detail="Aún no hay técnico asignado")

    res_tec = await db.execute(
        select(Tecnico)
        .options(
            undefer(Tecnico.latitud),
            undefer(Tecnico.longitud),
            undefer(Tecnico.ultima_actualizacion),
        )
        .where(Tecnico.id == asignacion.tecnico_id)
    )
    tecnico = res_tec.scalar_one_or_none()
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    return UbicacionTecnicoResponse(
        tecnico_id=tecnico.id,
        nombre=tecnico.nombre,
        latitud=tecnico.latitud,
        longitud=tecnico.longitud,
        ultima_actualizacion=tecnico.ultima_actualizacion,
        estado_asignacion=asignacion.estado,
        eta=asignacion.eta,
    )


# ── Helpers ───────────────────────────────────────────────────

async def _obtener_participantes_chat(
    asignacion: Asignacion, sender_id: int, db: AsyncSession
) -> list[int]:
    """Devuelve IDs de todos los participantes del chat EXCEPTO el remitente."""
    ids: set[int] = set()
    inc_res = await db.execute(select(Incidente.usuario_id).where(Incidente.id == asignacion.incidente_id))
    cliente_id = inc_res.scalar_one_or_none()
    if cliente_id:
        ids.add(cliente_id)
    taller_res = await db.execute(select(Taller.usuario_id).where(Taller.id == asignacion.taller_id))
    taller_uid = taller_res.scalar_one_or_none()
    if taller_uid:
        ids.add(taller_uid)
    if asignacion.tecnico_id:
        tec_res = await db.execute(select(Tecnico.usuario_id).where(Tecnico.id == asignacion.tecnico_id))
        tec_uid = tec_res.scalar_one_or_none()
        if tec_uid:
            ids.add(tec_uid)
    ids.discard(sender_id)
    return list(ids)


# ── CU18 · Chat ───────────────────────────────────────────────

async def _verificar_acceso_chat(
    user_id: int, role: str, asignacion: Asignacion, db: AsyncSession
) -> None:
    if role == "taller":
        res = await db.execute(select(Taller).where(Taller.usuario_id == user_id))
        taller = res.scalar_one_or_none()
        if not taller or asignacion.taller_id != taller.id:
            raise HTTPException(status_code=403, detail="No tienes acceso a esta conversación")
    elif role == "cliente":
        res = await db.execute(
            select(Incidente).where(
                Incidente.id == asignacion.incidente_id,
                Incidente.usuario_id == user_id,
            )
        )
        if not res.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="No tienes acceso a esta conversación")
    elif role == "tecnico":
        res = await db.execute(
            select(Tecnico).where(Tecnico.usuario_id == user_id, Tecnico.activo.is_(True))
        )
        tecnico = res.scalar_one_or_none()
        if not tecnico or asignacion.tecnico_id != tecnico.id:
            raise HTTPException(status_code=403, detail="No tienes acceso a esta conversación")
    else:
        raise HTTPException(status_code=403, detail="Acceso denegado")


async def enviar_mensaje(
    user_id: int, role: str, data: MensajeCreate, db: AsyncSession
) -> MensajeResponse:
    res_asig = await db.execute(
        select(Asignacion).where(Asignacion.id == data.asignacion_id)
    )
    asignacion = res_asig.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    await _verificar_acceso_chat(user_id, role, asignacion, db)

    mensaje = Mensaje(
        asignacion_id=data.asignacion_id,
        usuario_id=user_id,
        contenido=data.contenido,
    )
    db.add(mensaje)
    await db.commit()
    await db.refresh(mensaje)

    res_u = await db.execute(select(User).where(User.id == user_id))
    user = res_u.scalar_one()

    response = MensajeResponse(
        id=mensaje.id,
        asignacion_id=mensaje.asignacion_id,
        usuario_id=mensaje.usuario_id,
        remitente=user.full_name or user.username,
        rol=user.role,
        contenido=mensaje.contenido,
        created_at=mensaje.created_at,
    )

    dest_ids = await _obtener_participantes_chat(asignacion, user_id, db)
    from app.comunicacion.websocket import notify_many
    await notify_many(dest_ids, "nuevo_mensaje", response.model_dump(mode="json"))

    return response


async def listar_mensajes(
    asignacion_id: int, user_id: int, role: str, db: AsyncSession
) -> list[MensajeResponse]:
    res_asig = await db.execute(
        select(Asignacion).where(Asignacion.id == asignacion_id)
    )
    asignacion = res_asig.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    await _verificar_acceso_chat(user_id, role, asignacion, db)

    result = await db.execute(
        select(Mensaje, User)
        .join(User, Mensaje.usuario_id == User.id)
        .where(Mensaje.asignacion_id == asignacion_id)
        .order_by(Mensaje.created_at)
    )
    return [
        MensajeResponse(
            id=m.id,
            asignacion_id=m.asignacion_id,
            usuario_id=m.usuario_id,
            remitente=u.full_name or u.username,
            rol=u.role,
            contenido=m.contenido,
            created_at=m.created_at,
        )
        for m, u in result.all()
    ]
