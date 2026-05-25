import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.dependencies import get_current_user, require_role
from app.acceso_registro.models import User
from app.reportes import schemas, service

router = APIRouter()


# ── CU32 - Recordatorios de mantenimiento ─────────────────
@router.get("/mantenimiento")
async def recordatorios_mantenimiento(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.obtener_recordatorios_mantenimiento(current_user.id, db)


# ── CU23 - Calificaciones pendientes (servicios sin calificar) ─
@router.get("/calificaciones/pendientes")
async def calificaciones_pendientes(
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    from app.emergencias.models import Incidente
    from app.talleres_tecnicos.models import Asignacion
    from app.reportes.models import Calificacion
    from app.acceso_registro.models import Taller

    # Asignaciones finalizadas del cliente sin calificacion
    asig_r = await db.execute(
        select(Asignacion)
        .join(Incidente, Asignacion.incidente_id == Incidente.id)
        .where(
            Incidente.usuario_id == current_user.id,
            Asignacion.estado == "finalizado",
        )
    )
    asignaciones = asig_r.scalars().all()
    if not asignaciones:
        return []

    asig_ids = [a.id for a in asignaciones]
    cal_r = await db.execute(
        select(Calificacion.asignacion_id).where(Calificacion.asignacion_id.in_(asig_ids))
    )
    ya_calificadas = {r[0] for r in cal_r.all()}
    pendientes = [a for a in asignaciones if a.id not in ya_calificadas]
    if not pendientes:
        return []

    taller_ids = list({a.taller_id for a in pendientes})
    tal_r = await db.execute(select(Taller).where(Taller.id.in_(taller_ids)))
    talleres = {t.id: t.nombre for t in tal_r.scalars().all()}

    inc_ids = list({a.incidente_id for a in pendientes})
    inc_r = await db.execute(select(Incidente.id).where(Incidente.id.in_(inc_ids)))

    return [
        {
            "asignacion_id": a.id,
            "incidente_id": a.incidente_id,
            "taller_id": a.taller_id,
            "taller_nombre": talleres.get(a.taller_id),
            "fecha_finalizacion": a.created_at.isoformat() if a.created_at else None,
        }
        for a in pendientes
    ]


# ── CU23 - Calificar servicio ──────────────────────────────
@router.post("/calificaciones", response_model=schemas.CalificacionResponse)
async def calificar_servicio(
    data: schemas.CalificacionCreate,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    from app.emergencias.models import Incidente
    from app.talleres_tecnicos.models import Asignacion
    from app.reportes.models import Calificacion
    from app.acceso_registro.models import Taller

    asig_r = await db.execute(
        select(Asignacion)
        .join(Incidente, Asignacion.incidente_id == Incidente.id)
        .where(
            Asignacion.id == data.asignacion_id,
            Asignacion.estado == "finalizado",
            Incidente.usuario_id == current_user.id,
        )
    )
    asignacion = asig_r.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada, no finalizada o no te pertenece")

    cal_r = await db.execute(
        select(Calificacion).where(Calificacion.asignacion_id == data.asignacion_id)
    )
    if cal_r.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya calificaste este servicio")

    calificacion = Calificacion(
        asignacion_id=data.asignacion_id,
        cliente_id=current_user.id,
        taller_id=asignacion.taller_id,
        puntuacion=data.puntuacion,
        comentario=data.resena,
    )
    db.add(calificacion)
    await db.flush()

    avg_r = await db.execute(
        select(func.avg(Calificacion.puntuacion))
        .where(Calificacion.taller_id == asignacion.taller_id, Calificacion.estado == "activa")
    )
    tal_r = await db.execute(select(Taller).where(Taller.id == asignacion.taller_id))
    taller = tal_r.scalar_one_or_none()
    if taller:
        taller.rating = round(float(avg_r.scalar_one() or 0.0), 2)

    await db.commit()
    await db.refresh(calificacion)
    return schemas.CalificacionResponse.model_validate(calificacion)


# ── CU29 - Ver historial de servicios ─────────────────────
@router.get("/historial")
async def historial(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.models import ServicioRealizado, Asignacion
    from app.cotizacion_pagos.models import Cotizacion
    from app.emergencias.models import Incidente
    from sqlalchemy.orm import undefer as _undefer

    if current_user.role in ("taller", "tecnico"):
        from app.talleres_tecnicos.service import listar_servicios_realizados
        servicios = await listar_servicios_realizados(current_user.id, current_user.role, db)
    elif current_user.role == "cliente":
        res = await db.execute(
            select(ServicioRealizado)
            .join(Asignacion, ServicioRealizado.asignacion_id == Asignacion.id)
            .join(Incidente, Asignacion.incidente_id == Incidente.id)
            .where(Incidente.usuario_id == current_user.id)
            .order_by(ServicioRealizado.fecha_cierre.desc())
        )
        servicios = list(res.scalars().all())
    else:
        servicios = []

    if not servicios:
        return []

    asig_ids = [s.asignacion_id for s in servicios]

    # Mapear asignacion → incidente
    asig_res = await db.execute(
        select(Asignacion.id, Asignacion.incidente_id).where(Asignacion.id.in_(asig_ids))
    )
    asig_to_inc: dict[int, int] = {row[0]: row[1] for row in asig_res.all()}
    inc_ids = list(set(asig_to_inc.values()))

    # Info de incidentes (batch)
    inc_res = await db.execute(
        select(Incidente.id, Incidente.descripcion, Incidente.tipo_incidente)
        .options(_undefer(Incidente.tipo_incidente))
        .where(Incidente.id.in_(inc_ids))
    )
    inc_data: dict[int, dict] = {
        row[0]: {"incidente_id": row[0], "descripcion": row[1], "tipo_incidente": row[2]}
        for row in inc_res.all()
    }

    # Cotizaciones aceptadas/pagadas (batch)
    cot_res = await db.execute(
        select(Cotizacion.incidente_id, Cotizacion.monto_estimado, Cotizacion.estado)
        .where(
            Cotizacion.incidente_id.in_(inc_ids),
            Cotizacion.estado.in_(["aceptada", "pagada"]),
        )
    )
    cot_data: dict[int, float] = {row[0]: row[1] for row in cot_res.all()}

    return [
        {
            "id":                  s.id,
            "asignacion_id":       s.asignacion_id,
            "descripcion_trabajo": s.descripcion_trabajo,
            "repuestos":           s.repuestos,
            "observaciones":       s.observaciones,
            "fecha_cierre":        s.fecha_cierre.isoformat() if s.fecha_cierre else None,
            "monto_cotizacion":    cot_data.get(asig_to_inc.get(s.asignacion_id)),
            **(inc_data.get(asig_to_inc.get(s.asignacion_id, 0), {})),
        }
        for s in servicios
    ]


# ── CU26 - Métricas del taller ────────────────────────────
@router.get("/metricas/taller")
async def metricas_taller(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.models import Asignacion, ServicioRealizado
    from app.talleres_tecnicos.service import get_taller_by_user
    from app.emergencias.models import Incidente
    from app.cotizacion_pagos.models import Cotizacion, Pago
    from app.reportes.models import Calificacion
    from sqlalchemy.orm import undefer as _ud

    taller = await get_taller_by_user(current_user.id, db)

    total_asig_r = await db.execute(
        select(func.count(Asignacion.id)).where(Asignacion.taller_id == taller.id)
    )
    total_asignaciones = total_asig_r.scalar_one()

    total_srv_r = await db.execute(
        select(func.count(ServicioRealizado.id))
        .join(Asignacion, ServicioRealizado.asignacion_id == Asignacion.id)
        .where(Asignacion.taller_id == taller.id)
    )
    total_servicios = total_srv_r.scalar_one()

    total_can_r = await db.execute(
        select(func.count(Asignacion.id))
        .where(Asignacion.taller_id == taller.id, Asignacion.estado == "cancelado")
    )
    total_canceladas = total_can_r.scalar_one()

    total_ing_r = await db.execute(
        select(func.coalesce(func.sum(Pago.monto), 0.0))
        .join(Cotizacion, Pago.cotizacion_id == Cotizacion.id)
        .where(Cotizacion.taller_id == taller.id)
    )
    total_ingresos = float(total_ing_r.scalar_one() or 0.0)

    cal_r = await db.execute(
        select(func.avg(Calificacion.puntuacion), func.count(Calificacion.id))
        .where(Calificacion.taller_id == taller.id, Calificacion.estado == "activa")
    )
    cal_row = cal_r.first()
    promedio_cal = round(float(cal_row[0] or 0.0), 2)
    total_cal = cal_row[1]

    tipo_r = await db.execute(
        select(Incidente.tipo_incidente, func.count(Incidente.id))
        .options(_ud(Incidente.tipo_incidente))
        .join(Asignacion, Asignacion.incidente_id == Incidente.id)
        .where(Asignacion.taller_id == taller.id)
        .group_by(Incidente.tipo_incidente)
    )
    por_tipo = {(r[0] or "sin_clasificar"): r[1] for r in tipo_r.all()}

    tasa = round((total_servicios / total_asignaciones * 100) if total_asignaciones > 0 else 0.0, 1)

    return {
        "taller_id": taller.id,
        "nombre": taller.nombre,
        "rating": taller.rating,
        "total_asignaciones": total_asignaciones,
        "total_servicios_completados": total_servicios,
        "total_canceladas": total_canceladas,
        "tasa_completado_pct": tasa,
        "total_ingresos": total_ingresos,
        "comision_plataforma": round(total_ingresos * 0.10, 2),
        "promedio_calificacion": promedio_cal,
        "total_calificaciones": total_cal,
        "servicios_por_tipo": por_tipo,
    }


# ── CU33 - Métricas globales (admin) ──────────────────────
@router.get("/metricas/globales")
async def metricas_globales(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.models import Asignacion, ServicioRealizado
    from app.emergencias.models import Incidente
    from app.cotizacion_pagos.models import Pago
    from app.acceso_registro.models import Taller
    from sqlalchemy.orm import undefer as _ud

    inc_estado_r = await db.execute(
        select(Incidente.estado, func.count(Incidente.id)).group_by(Incidente.estado)
    )
    por_estado = {r[0]: r[1] for r in inc_estado_r.all()}

    inc_tipo_r = await db.execute(
        select(Incidente.tipo_incidente, func.count(Incidente.id))
        .options(_ud(Incidente.tipo_incidente))
        .group_by(Incidente.tipo_incidente)
    )
    por_tipo = {(r[0] or "sin_clasificar"): r[1] for r in inc_tipo_r.all()}

    inc_prio_r = await db.execute(
        select(Incidente.prioridad, func.count(Incidente.id)).group_by(Incidente.prioridad)
    )
    por_prioridad = {r[0]: r[1] for r in inc_prio_r.all()}

    usr_r = await db.execute(
        select(User.role, func.count(User.id))
        .where(User.is_active.is_(True))
        .group_by(User.role)
    )
    por_rol = {r[0]: r[1] for r in usr_r.all()}

    tal_r = await db.execute(
        select(Taller.estado, func.count(Taller.id)).group_by(Taller.estado)
    )
    talleres_por_estado = {r[0]: r[1] for r in tal_r.all()}

    srv_r = await db.execute(select(func.count(ServicioRealizado.id)))
    total_servicios = srv_r.scalar_one()

    ing_r = await db.execute(
        select(func.coalesce(func.sum(Pago.monto), 0.0))
    )
    total_ingresos = float(ing_r.scalar_one() or 0.0)

    return {
        "incidentes_por_estado": por_estado,
        "incidentes_por_tipo": por_tipo,
        "incidentes_por_prioridad": por_prioridad,
        "usuarios_por_rol": por_rol,
        "talleres_por_estado": talleres_por_estado,
        "total_servicios_completados": total_servicios,
        "total_ingresos_plataforma": total_ingresos,
        "comision_plataforma_total": round(total_ingresos * 0.10, 2),
    }


# ── CU35 - Auditoría / Bitácora ────────────────────────────
@router.get("/auditoria", response_model=schemas.AuditoriaListResponse)
async def listar_auditoria(
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    accion: Optional[str] = Query(None),
    usuario_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.listar_eventos(db, desde, hasta, accion, usuario_id, page, size)
    pages = math.ceil(total / size) if total > 0 else 1
    return schemas.AuditoriaListResponse(
        items=[schemas.BitacoraEventoResponse.model_validate(e) for e in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/auditoria/exportar")
async def exportar_auditoria(
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    accion: Optional[str] = Query(None),
    usuario_id: Optional[int] = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    csv_content = await service.exportar_csv(db, desde, hasta, accion, usuario_id)
    headers = {
        "Content-Disposition": "attachment; filename=auditoria.csv",
        "Content-Type": "text/csv; charset=utf-8",
    }
    return Response(content=csv_content.encode("utf-8"), headers=headers)


@router.get("/auditoria/{evento_id}", response_model=schemas.BitacoraEventoResponse)
async def detalle_evento(
    evento_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    evento = await service.obtener_evento(evento_id, db)
    if not evento:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return schemas.BitacoraEventoResponse.model_validate(evento)
