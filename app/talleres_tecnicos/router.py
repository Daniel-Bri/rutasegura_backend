from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from app.db.session import get_db
from app.core.dependencies import get_current_user, require_role
from app.acceso_registro.models import User
from app.talleres_tecnicos import schemas, service

router = APIRouter()


# ── CU16 · Info del taller propio ────────────────────────
@router.get("/mi-taller", response_model=schemas.TallerInfoResponse)
async def mi_taller(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_taller_info(current_user.id, db)


# ── CU16 · Actualizar disponibilidad ─────────────────────
@router.patch("/disponibilidad", response_model=schemas.TallerInfoResponse)
async def actualizar_disponibilidad(
    data: schemas.DisponibilidadUpdate,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    return await service.actualizar_disponibilidad(current_user.id, data.disponible, db)


# ── CU25 · Listar técnicos ─────────────────────────────────
@router.get("/tecnicos", response_model=list[schemas.TecnicoResponse])
async def listar_tecnicos(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.get_taller_by_user(current_user.id, db)
    tecnicos = await service.listar_tecnicos(taller.id, db)
    return [schemas.TecnicoResponse.model_validate(t) for t in tecnicos]


# ── CU25 · Registrar técnico ───────────────────────────────
@router.post("/tecnicos", response_model=schemas.TecnicoResponse, status_code=status.HTTP_201_CREATED)
async def registrar_tecnico(
    data: schemas.TecnicoCreate,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.get_taller_by_user(current_user.id, db)
    tecnico = await service.registrar_tecnico(taller.id, data, db)
    return schemas.TecnicoResponse.model_validate(tecnico)


# ── CU25 · Editar técnico ──────────────────────────────────
@router.patch("/tecnicos/{tecnico_id}", response_model=schemas.TecnicoResponse)
async def actualizar_tecnico(
    tecnico_id: int,
    data: schemas.TecnicoUpdate,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.get_taller_by_user(current_user.id, db)
    tecnico = await service.actualizar_tecnico(tecnico_id, taller.id, data, db)
    return schemas.TecnicoResponse.model_validate(tecnico)


# ── CU25 · Desactivar técnico ──────────────────────────────
@router.delete("/tecnicos/{tecnico_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desactivar_tecnico(
    tecnico_id: int,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.get_taller_by_user(current_user.id, db)
    await service.desactivar_tecnico(tecnico_id, taller.id, db)


# ── CU15 · Asignaciones activas ───────────────────────────
@router.get("/asignaciones/activas", response_model=list[schemas.AsignacionResponse])
async def listar_asignaciones_activas(
    current_user: User = Depends(require_role("taller", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    from app.emergencias.models import Incidente
    asignaciones = await service.listar_asignaciones_activas(current_user.id, current_user.role, db)
    if not asignaciones:
        return []

    # Batch-fetch descripciones para detectar incidentes SOS
    inc_ids = [a.incidente_id for a in asignaciones]
    inc_rows = await db.execute(
        select(Incidente.id, Incidente.descripcion).where(Incidente.id.in_(inc_ids))
    )
    inc_desc = {row.id: (row.descripcion or "") for row in inc_rows.all()}

    responses = []
    for a in asignaciones:
        r = schemas.AsignacionResponse.model_validate(a)
        r.es_sos = "SOS" in inc_desc.get(a.incidente_id, "")
        responses.append(r)
    return responses


# ── CU15 · Actualizar estado del servicio ─────────────────
@router.patch("/asignaciones/{asignacion_id}/estado", response_model=schemas.AsignacionResponse)
async def actualizar_estado_asignacion(
    asignacion_id: int,
    data: schemas.AsignacionEstadoUpdate,
    current_user: User = Depends(require_role("taller", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    asignacion = await service.actualizar_estado_asignacion(
        asignacion_id, current_user.id, current_user.role, data, db
    )

    try:
        from app.comunicacion.models import Notificacion
        from app.comunicacion.websocket import notify
        from app.notificaciones.service import notificar_usuario
        from app.emergencias.models import Incidente
        inc_r = await db.execute(select(Incidente).where(Incidente.id == asignacion.incidente_id))
        inc = inc_r.scalar_one_or_none()
        if inc:
            etiquetas = {
                "en_camino":     "El técnico está en camino",
                "en_sitio":      "El técnico llegó a tu ubicación",
                "en_reparacion": "El técnico está atendiendo tu vehículo",
                "finalizado":    "Tu servicio fue completado exitosamente",
                "cancelado":     "El servicio fue cancelado",
            }
            titulo = etiquetas.get(data.estado, f"Estado actualizado: {data.estado}")
            notif_msg = f"Estado de tu emergencia #{inc.id}: {data.estado}."
            db.add(Notificacion(
                usuario_id=inc.usuario_id,
                titulo=titulo,
                mensaje=notif_msg,
                tipo="estado",
                referencia_id=inc.id,
            ))
            await db.commit()
            await notify(inc.usuario_id, "notificacion", {
                "titulo": titulo, "mensaje": notif_msg,
                "tipo": "estado", "referencia_id": inc.id,
            })
            await notify(inc.usuario_id, "estado_asignacion", {
                "asignacion_id": asignacion.id, "estado": data.estado,
            })
            await notificar_usuario(
                inc.usuario_id, titulo, notif_msg, db,
                {"tipo": "estado", "estado": data.estado, "asignacion_id": str(asignacion_id)},
            )

            if data.estado == "finalizado":
                from app.cotizacion_pagos.models import Cotizacion
                cot_r = await db.execute(
                    select(Cotizacion).where(
                        Cotizacion.incidente_id == asignacion.incidente_id,
                        Cotizacion.estado == "aceptada",
                    )
                )
                cot = cot_r.scalar_one_or_none()
                if cot:
                    await notify(inc.usuario_id, "solicitud_pago", {
                        "cotizacion_id": cot.id,
                        "monto": cot.monto_estimado,
                        "incidente_id": asignacion.incidente_id,
                        "asignacion_id": asignacion_id,
                    })
    except Exception:
        pass

    return schemas.AsignacionResponse.model_validate(asignacion)


# ── CU22 · Asignaciones listas para cierre ────────────────
@router.get("/servicios/listas", response_model=list[schemas.AsignacionResponse])
async def asignaciones_listas(
    current_user: User = Depends(require_role("taller", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    asignaciones = await service.listar_asignaciones_listas(current_user.id, current_user.role, db)
    return [schemas.AsignacionResponse.model_validate(a) for a in asignaciones]


# ── CU22 · Registrar servicio realizado y cierre ──────────
@router.post("/servicios", response_model=schemas.ServicioRealizadoResponse, status_code=status.HTTP_201_CREATED)
async def registrar_servicio(
    data: schemas.ServicioRealizadoCreate,
    current_user: User = Depends(require_role("taller", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    servicio = await service.registrar_servicio_y_cerrar(current_user.id, current_user.role, data, db)
    return schemas.ServicioRealizadoResponse.model_validate(servicio)


# ── CU22 · Historial de servicios realizados ──────────────
@router.get("/servicios", response_model=list[schemas.ServicioRealizadoResponse])
async def listar_servicios(
    current_user: User = Depends(require_role("taller", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    servicios = await service.listar_servicios_realizados(current_user.id, current_user.role, db)
    return [schemas.ServicioRealizadoResponse.model_validate(s) for s in servicios]


# ── CU25 · Solicitudes pendientes de asignar técnico ───────
@router.get("/asignaciones/pendientes", response_model=list[schemas.AsignacionResponse])
async def listar_asignaciones_pendientes(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.get_taller_by_user(current_user.id, db)
    asignaciones = await service.listar_asignaciones_sin_tecnico(taller.id, db)
    return [schemas.AsignacionResponse.model_validate(a) for a in asignaciones]


# ── CU25 · Asignar técnico a solicitud ────────────────────
@router.patch("/asignaciones/{asignacion_id}/asignar-tecnico", response_model=schemas.AsignacionResponse)
async def asignar_tecnico(
    asignacion_id: int,
    data: schemas.AsignarTecnicoPayload,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.get_taller_by_user(current_user.id, db)
    asignacion = await service.asignar_tecnico_a_solicitud(asignacion_id, taller.id, data.tecnico_id, db)
    return schemas.AsignacionResponse.model_validate(asignacion)


# ── CU nuevo · Registrar cobro por visita ────────────────
@router.post("/asignaciones/{asignacion_id}/cobro-visita")
async def registrar_cobro_visita(
    asignacion_id: int,
    data: schemas.CobroVisitaCreate,
    current_user: User = Depends(require_role("taller", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.models import CobroVisita, Tecnico
    from sqlalchemy import select as _sel

    # Verificar que la asignación existe y pertenece al taller
    asig_r = await db.execute(_sel(Asignacion).where(Asignacion.id == asignacion_id))
    asig = asig_r.scalar_one_or_none()
    if not asig:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    if current_user.role == "taller":
        taller = await service.get_taller_by_user(current_user.id, db)
        if asig.taller_id != taller.id:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="No tienes acceso a esta asignación")
        tecnico_id = None
    else:
        tec_r = await db.execute(_sel(Tecnico).where(Tecnico.usuario_id == current_user.id))
        tec = tec_r.scalar_one_or_none()
        tecnico_id = tec.id if tec else None

    # No puede haber ya un cobro
    existing = await db.execute(_sel(CobroVisita).where(CobroVisita.asignacion_id == asignacion_id))
    if existing.scalar_one_or_none():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Ya existe un cobro por visita para esta asignación")

    cobro = CobroVisita(
        asignacion_id=asignacion_id,
        tecnico_id=tecnico_id,
        monto=data.monto,
        concepto=data.concepto or "Cobro por desplazamiento al sitio",
    )
    db.add(cobro)

    # Notificar al cliente
    try:
        from app.emergencias.models import Incidente
        from app.notificaciones.service import notificar_usuario
        inc_r = await db.execute(_sel(Incidente).where(Incidente.id == asig.incidente_id))
        inc = inc_r.scalar_one_or_none()
        if inc:
            await notificar_usuario(
                inc.usuario_id,
                "💰 Cobro por visita registrado",
                f"El taller registró un cobro por visita de Bs. {data.monto:.2f}. {data.concepto or ''}",
                db,
                {"tipo": "cobro_visita", "asignacion_id": str(asignacion_id)},
            )
    except Exception:
        pass

    await db.commit()
    await db.refresh(cobro)
    return {"id": cobro.id, "asignacion_id": cobro.asignacion_id,
            "monto": cobro.monto, "concepto": cobro.concepto, "created_at": cobro.created_at}


# ── CU31 · Confirmar llegada del técnico (cliente) ─────────
@router.patch("/asignaciones/{asignacion_id}/confirmar-llegada", response_model=schemas.AsignacionResponse)
async def confirmar_llegada(
    asignacion_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asignacion = await service.confirmar_llegada_tecnico(asignacion_id, current_user.id, db)
    return schemas.AsignacionResponse.model_validate(asignacion)
