import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException

from app.talleres_tecnicos.models import Tecnico, Asignacion, ServicioRealizado
from app.acceso_registro.models import Taller
from app.talleres_tecnicos.schemas import (
    TecnicoCreate, TecnicoUpdate, TallerInfoResponse,
    AsignacionEstadoUpdate, TRANSICIONES_VALIDAS,
    ServicioRealizadoCreate,
)


async def get_taller_by_user(user_id: int, db: AsyncSession) -> Taller:
    result = await db.execute(
        select(Taller).where(Taller.usuario_id == user_id, Taller.estado == "aprobado")
    )
    taller = result.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=403, detail="No tienes un taller aprobado")
    return taller


# ── Técnicos ───────────────────────────────────────────────
async def listar_tecnicos(taller_id: int, db: AsyncSession) -> list[Tecnico]:
    result = await db.execute(
        select(Tecnico).where(Tecnico.taller_id == taller_id, Tecnico.activo.is_(True))
    )
    tecnicos = list(result.scalars().all())

    # Auto-corregir técnicos "ocupado" sin asignación activa real
    ocupados = [t for t in tecnicos if t.estado == "ocupado"]
    if ocupados:
        res = await db.execute(
            select(Asignacion.tecnico_id)
            .where(
                Asignacion.tecnico_id.in_([t.id for t in ocupados]),
                Asignacion.estado.notin_(["cancelado", "finalizado"]),
            )
            .distinct()
        )
        con_asignacion = {row[0] for row in res.all()}
        corregido = False
        for t in ocupados:
            if t.id not in con_asignacion:
                t.estado = "disponible"
                corregido = True
        if corregido:
            await db.commit()

    return tecnicos


async def _resolver_usuario_id(email: str | None, db: AsyncSession) -> int | None:
    """Busca un User con role='tecnico' por email para vincular al Tecnico."""
    if not email:
        return None
    from app.acceso_registro.models import User
    res = await db.execute(
        select(User).where(User.email == email.strip().lower(), User.role == "tecnico")
    )
    user = res.scalar_one_or_none()
    return user.id if user else None


async def registrar_tecnico(taller_id: int, data: TecnicoCreate, db: AsyncSession) -> Tecnico:
    usuario_id = await _resolver_usuario_id(data.email, db)
    tecnico = Tecnico(
        taller_id=taller_id,
        nombre=data.nombre,
        especialidad=data.especialidad,
        telefono=data.telefono,
        usuario_id=usuario_id,
    )
    db.add(tecnico)
    await db.commit()
    await db.refresh(tecnico)
    return tecnico


async def actualizar_tecnico(
    tecnico_id: int, taller_id: int, data: TecnicoUpdate, db: AsyncSession
) -> Tecnico:
    result = await db.execute(
        select(Tecnico).where(Tecnico.id == tecnico_id, Tecnico.taller_id == taller_id, Tecnico.activo.is_(True))
    )
    tecnico = result.scalar_one_or_none()
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    if data.nombre is not None:       tecnico.nombre       = data.nombre.strip()
    if data.especialidad is not None: tecnico.especialidad = data.especialidad.strip()
    if data.telefono is not None:     tecnico.telefono     = data.telefono
    if data.estado is not None:       tecnico.estado       = data.estado
    if data.email is not None:
        tecnico.usuario_id = await _resolver_usuario_id(data.email, db)

    await db.commit()
    await db.refresh(tecnico)
    return tecnico


async def desactivar_tecnico(tecnico_id: int, taller_id: int, db: AsyncSession) -> None:
    result = await db.execute(
        select(Tecnico).where(Tecnico.id == tecnico_id, Tecnico.taller_id == taller_id)
    )
    tecnico = result.scalar_one_or_none()
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    tecnico.activo = False
    await db.commit()


# ── Asignaciones ───────────────────────────────────────────
async def listar_asignaciones_sin_tecnico(taller_id: int, db: AsyncSession) -> list[Asignacion]:
    result = await db.execute(
        select(Asignacion).where(
            Asignacion.taller_id == taller_id,
            Asignacion.tecnico_id == None,
            Asignacion.estado == "aceptado",
        )
    )
    return list(result.scalars().all())


# ── CU16 · Disponibilidad ──────────────────────────────────
async def get_taller_info(user_id: int, db: AsyncSession) -> TallerInfoResponse:
    result = await db.execute(select(Taller).where(Taller.usuario_id == user_id))
    taller = result.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=404, detail="No tienes un taller registrado")

    result = await db.execute(
        select(Tecnico).where(Tecnico.taller_id == taller.id, Tecnico.activo.is_(True))
    )
    tecnicos = list(result.scalars().all())

    return TallerInfoResponse(
        id=taller.id,
        nombre=taller.nombre,
        direccion=taller.direccion,
        telefono=taller.telefono,
        email_comercial=taller.email_comercial,
        disponible=taller.disponible,
        estado=taller.estado,
        rating=taller.rating,
        total_tecnicos=len(tecnicos),
        tecnicos_disponibles=sum(1 for t in tecnicos if t.estado == "disponible"),
        tecnicos_ocupados=sum(1 for t in tecnicos if t.estado == "ocupado"),
    )


async def actualizar_disponibilidad(user_id: int, disponible: bool, db: AsyncSession) -> TallerInfoResponse:
    result = await db.execute(select(Taller).where(Taller.usuario_id == user_id))
    taller = result.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=404, detail="No tienes un taller registrado")
    if taller.estado != "aprobado":
        raise HTTPException(status_code=400, detail="Tu taller aún no está aprobado por el administrador")

    taller.disponible = disponible
    await db.commit()
    await db.refresh(taller)
    return await get_taller_info(user_id, db)


# ── CU15 · Estado del servicio ─────────────────────────────
_ESTADOS_ACTIVOS = ["aceptado", "en_camino", "en_sitio", "en_reparacion"]


async def listar_asignaciones_activas(user_id: int, role: str, db: AsyncSession) -> list[Asignacion]:
    if role == "taller":
        taller = await get_taller_by_user(user_id, db)
        result = await db.execute(
            select(Asignacion)
            .where(Asignacion.taller_id == taller.id, Asignacion.estado.in_(_ESTADOS_ACTIVOS))
            .order_by(Asignacion.created_at.desc())
        )
    else:  # tecnico
        result_t = await db.execute(
            select(Tecnico).where(Tecnico.usuario_id == user_id, Tecnico.activo.is_(True))
        )
        tecnico = result_t.scalar_one_or_none()
        if not tecnico:
            return []
        result = await db.execute(
            select(Asignacion)
            .where(Asignacion.tecnico_id == tecnico.id, Asignacion.estado.in_(_ESTADOS_ACTIVOS))
            .order_by(Asignacion.created_at.desc())
        )
    return list(result.scalars().all())


async def actualizar_estado_asignacion(
    asignacion_id: int, user_id: int, role: str, data: AsignacionEstadoUpdate, db: AsyncSession
) -> Asignacion:
    result = await db.execute(select(Asignacion).where(Asignacion.id == asignacion_id))
    asignacion = result.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    if role == "taller":
        taller = await get_taller_by_user(user_id, db)
        if asignacion.taller_id != taller.id:
            raise HTTPException(status_code=403, detail="No tienes permiso sobre esta asignación")
    else:  # tecnico
        result_t = await db.execute(
            select(Tecnico).where(Tecnico.usuario_id == user_id, Tecnico.activo.is_(True))
        )
        tecnico = result_t.scalar_one_or_none()
        if not tecnico or asignacion.tecnico_id != tecnico.id:
            raise HTTPException(status_code=403, detail="No tienes permiso sobre esta asignación")

    permitidos = TRANSICIONES_VALIDAS.get(asignacion.estado, set())
    if data.estado not in permitidos:
        raise HTTPException(
            status_code=400,
            detail=f"Transición inválida: '{asignacion.estado}' → '{data.estado}'. "
                   f"Permitidos: {', '.join(sorted(permitidos)) or 'ninguno'}",
        )

    asignacion.estado = data.estado
    if data.observacion:
        asignacion.observacion = data.observacion

    # Liberar técnico al finalizar o cancelar
    if data.estado in ("finalizado", "cancelado") and asignacion.tecnico_id:
        res_tec = await db.execute(
            select(Tecnico).where(Tecnico.id == asignacion.tecnico_id)
        )
        tec = res_tec.scalar_one_or_none()
        if tec and tec.estado == "ocupado":
            tec.estado = "disponible"

    await db.commit()
    await db.refresh(asignacion)
    return asignacion


# ── CU22 · Servicio Realizado ──────────────────────────────
async def listar_asignaciones_listas(user_id: int, role: str, db: AsyncSession) -> list[Asignacion]:
    """Asignaciones en estado en_reparacion listas para cierre formal."""
    if role == "taller":
        taller = await get_taller_by_user(user_id, db)
        result = await db.execute(
            select(Asignacion)
            .where(Asignacion.taller_id == taller.id, Asignacion.estado == "en_reparacion")
            .order_by(Asignacion.created_at.desc())
        )
    else:  # tecnico
        result_t = await db.execute(
            select(Tecnico).where(Tecnico.usuario_id == user_id, Tecnico.activo.is_(True))
        )
        tecnico = result_t.scalar_one_or_none()
        if not tecnico:
            return []
        result = await db.execute(
            select(Asignacion)
            .where(Asignacion.tecnico_id == tecnico.id, Asignacion.estado == "en_reparacion")
            .order_by(Asignacion.created_at.desc())
        )
    return list(result.scalars().all())


async def registrar_servicio_y_cerrar(
    user_id: int, role: str, data: ServicioRealizadoCreate, db: AsyncSession
) -> ServicioRealizado:
    result = await db.execute(select(Asignacion).where(Asignacion.id == data.asignacion_id))
    asignacion = result.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    # Verificar permiso
    if role == "taller":
        taller = await get_taller_by_user(user_id, db)
        if asignacion.taller_id != taller.id:
            raise HTTPException(status_code=403, detail="No tienes permiso sobre esta asignación")
    else:
        result_t = await db.execute(
            select(Tecnico).where(Tecnico.usuario_id == user_id, Tecnico.activo.is_(True))
        )
        tecnico = result_t.scalar_one_or_none()
        if not tecnico or asignacion.tecnico_id != tecnico.id:
            raise HTTPException(status_code=403, detail="No tienes permiso sobre esta asignación")

    if asignacion.estado not in ("en_reparacion", "en_sitio", "en_camino", "aceptado"):
        raise HTTPException(status_code=400, detail="La asignación no está en un estado activo que permita cierre")

    # Verificar duplicado
    dup = await db.execute(
        select(ServicioRealizado).where(ServicioRealizado.asignacion_id == data.asignacion_id)
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Este servicio ya fue registrado y cerrado")

    repuestos_json = (
        json.dumps([r.model_dump() for r in data.repuestos], ensure_ascii=False)
        if data.repuestos else None
    )
    servicio = ServicioRealizado(
        asignacion_id=data.asignacion_id,
        descripcion_trabajo=data.descripcion_trabajo,
        repuestos=repuestos_json,
        observaciones=data.observaciones,
    )
    db.add(servicio)

    asignacion.estado = "finalizado"

    # Liberar al técnico
    if asignacion.tecnico_id:
        result_t2 = await db.execute(
            select(Tecnico).where(Tecnico.id == asignacion.tecnico_id)
        )
        tec = result_t2.scalar_one_or_none()
        if tec:
            tec.estado = "disponible"

    await db.commit()
    await db.refresh(servicio)
    return servicio


async def listar_servicios_realizados(user_id: int, role: str, db: AsyncSession) -> list[ServicioRealizado]:
    if role == "taller":
        taller = await get_taller_by_user(user_id, db)
        sub = select(Asignacion.id).where(Asignacion.taller_id == taller.id)
    else:
        result_t = await db.execute(
            select(Tecnico).where(Tecnico.usuario_id == user_id, Tecnico.activo.is_(True))
        )
        tecnico = result_t.scalar_one_or_none()
        if not tecnico:
            return []
        sub = select(Asignacion.id).where(Asignacion.tecnico_id == tecnico.id)

    result = await db.execute(
        select(ServicioRealizado)
        .where(ServicioRealizado.asignacion_id.in_(sub))
        .order_by(ServicioRealizado.fecha_cierre.desc())
    )
    return list(result.scalars().all())


# ── CU31 · Confirmar llegada del técnico (cliente) ─────────
async def confirmar_llegada_tecnico(
    asignacion_id: int, usuario_id: int, db: AsyncSession
) -> Asignacion:
    from app.emergencias.models import Incidente
    result = await db.execute(select(Asignacion).where(Asignacion.id == asignacion_id))
    asignacion = result.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    # Verificar que el incidente pertenece al usuario
    inc_result = await db.execute(
        select(Incidente).where(
            Incidente.id == asignacion.incidente_id,
            Incidente.usuario_id == usuario_id,
        )
    )
    if not inc_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="No tienes permiso sobre esta asignación")

    if asignacion.estado == "en_sitio":
        raise HTTPException(status_code=400, detail="La llegada del técnico ya fue confirmada")

    if asignacion.estado not in ("aceptado", "en_camino"):
        raise HTTPException(
            status_code=400,
            detail=f"No puedes confirmar llegada en estado '{asignacion.estado}'",
        )

    asignacion.estado = "en_sitio"
    await db.commit()
    await db.refresh(asignacion)
    return asignacion


async def asignar_tecnico_a_solicitud(
    asignacion_id: int, taller_id: int, tecnico_id: int, db: AsyncSession
) -> Asignacion:
    result = await db.execute(
        select(Asignacion).where(Asignacion.id == asignacion_id, Asignacion.taller_id == taller_id)
    )
    asignacion = result.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    result = await db.execute(
        select(Tecnico).where(
            Tecnico.id == tecnico_id,
            Tecnico.taller_id == taller_id,
            Tecnico.activo.is_(True),
            Tecnico.estado == "disponible",
        )
    )
    tecnico = result.scalar_one_or_none()
    if not tecnico:
        raise HTTPException(status_code=400, detail="Técnico no disponible o no pertenece a tu taller")

    asignacion.tecnico_id = tecnico_id
    tecnico.estado = "ocupado"
    await db.commit()
    await db.refresh(asignacion)
    return asignacion
