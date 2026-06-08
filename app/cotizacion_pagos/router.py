from fastapi import APIRouter, Depends, status # type: ignore
from sqlalchemy import select # type: ignore
from sqlalchemy.ext.asyncio import AsyncSession # type: ignore

from app.db.session import get_db
from app.core.dependencies import get_current_user, require_role
from app.acceso_registro.models import User
from app.cotizacion_pagos import schemas, service

router = APIRouter()


# ── CU20 · Incidentes disponibles para cotizar ─────────────
@router.get("/incidentes-disponibles", response_model=list[schemas.IncidenteDisponibleResponse])
async def incidentes_disponibles(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.service import get_taller_by_user
    taller = await get_taller_by_user(current_user.id, db)
    return await service.listar_incidentes_disponibles(taller.id, db)


# ── CU20 · Generar cotización ──────────────────────────────
@router.post("/cotizaciones", response_model=schemas.CotizacionResponse, status_code=status.HTTP_201_CREATED)
async def generar_cotizacion(
    data: schemas.CotizacionCreate,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select as _sel
    from app.talleres_tecnicos.service import get_taller_by_user
    taller = await get_taller_by_user(current_user.id, db)
    cotizacion = await service.generar_cotizacion(taller.id, data, db)

    try:
        from app.notificaciones.service import notificar_usuario
        from app.emergencias.models import Incidente as _Inc
        i_r = await db.execute(_sel(_Inc.usuario_id).where(_Inc.id == cotizacion.incidente_id))
        i_row = i_r.first()
        if i_row:
            monto = f"Bs. {cotizacion.monto_estimado:.2f}" if cotizacion.monto_estimado else ""
            await notificar_usuario(
                i_row[0],
                "📋 Nueva cotización recibida",
                f"El taller envió una cotización {monto}. Revísala en la app.",
                db,
                {"tipo": "nueva_cotizacion", "cotizacion_id": str(cotizacion.id)},
            )
    except Exception:
        pass

    from app.reportes.service import log_evento
    await log_evento(db, accion="generar_cotizacion", usuario_id=current_user.id,
                     usuario_nombre=getattr(current_user, 'username', None), entidad="cotizacion",
                     entidad_id=cotizacion.id,
                     detalle={"incidente_id": cotizacion.incidente_id,
                              "monto": float(cotizacion.monto_estimado or 0),
                              "tiempo_estimado_horas": data.tiempo_estimado_horas})
    return schemas.CotizacionResponse.model_validate(cotizacion)


# ── CU20 · Listar cotizaciones del taller ─────────────────
@router.get("/cotizaciones", response_model=list[schemas.CotizacionResponse])
async def listar_cotizaciones(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.service import get_taller_by_user
    taller = await get_taller_by_user(current_user.id, db)
    cotizaciones = await service.listar_cotizaciones(taller.id, db)
    return [schemas.CotizacionResponse.model_validate(c) for c in cotizaciones]


# ── CU20 · Mis cotizaciones (cliente) — enriquecido CU39/CU43 ─
@router.get("/mis-cotizaciones", response_model=list[schemas.CotizacionResponse])
async def mis_cotizaciones(
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.models import Asignacion
    from app.acceso_registro.models import Taller
    cotizaciones = await service.listar_mis_cotizaciones(current_user.id, db)

    taller_ids = list({c.taller_id for c in cotizaciones})
    talleres_map: dict[int, Taller] = {}
    if taller_ids:
        tal_res = await db.execute(select(Taller).where(Taller.id.in_(taller_ids)))
        talleres_map = {t.id: t for t in tal_res.scalars().all()}

    result = []
    for c in cotizaciones:
        asig_res = await db.execute(
            select(Asignacion)
            .where(Asignacion.incidente_id == c.incidente_id,
                   Asignacion.taller_id == c.taller_id)
            .order_by(Asignacion.created_at.desc())
        )
        asig = asig_res.scalars().first()
        taller = talleres_map.get(c.taller_id)
        r = schemas.CotizacionResponse.model_validate(c)
        r.estado_asignacion = asig.estado if asig else None
        r.taller_nombre = taller.nombre if taller else None
        r.taller_rating = taller.rating if taller else None
        result.append(r)
    return result


# ── CU20 · Ver cotización por ID ───────────────────────────
@router.get("/cotizaciones/{cotizacion_id}", response_model=schemas.CotizacionResponse)
async def ver_cotizacion(
    cotizacion_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cotizacion = await service.get_cotizacion(cotizacion_id, db)
    return schemas.CotizacionResponse.model_validate(cotizacion)


# ── CU20 · Confirmar / Rechazar cotización ─────────────────
@router.patch("/cotizaciones/{cotizacion_id}/estado", response_model=schemas.CotizacionResponse)
async def actualizar_estado_cotizacion(
    cotizacion_id: int,
    data: schemas.CotizacionEstadoUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cotizacion = await service.actualizar_estado(cotizacion_id, data.estado, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion=f"cotizacion_{data.estado}", usuario_id=current_user.id,
                     usuario_nombre=getattr(current_user, 'username', None), entidad="cotizacion",
                     entidad_id=cotizacion_id,
                     detalle={"nuevo_estado": data.estado, "incidente_id": cotizacion.incidente_id})
    return schemas.CotizacionResponse.model_validate(cotizacion)


# ── CU20 · Realizar pago (cliente) ────────────────────────
@router.post("/pagos", response_model=schemas.PagoResponse, status_code=status.HTTP_201_CREATED)
async def realizar_pago(
    data: schemas.PagoCreate,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    pago = await service.realizar_pago(current_user.id, data, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="realizar_pago", usuario_id=current_user.id,
                     usuario_nombre=getattr(current_user, 'username', None), entidad="pago",
                     entidad_id=pago.id,
                     detalle={"cotizacion_id": data.cotizacion_id,
                              "monto": float(pago.monto), "metodo": pago.metodo})
    return schemas.PagoResponse.model_validate(pago)


# ── CU26 · Ver comisiones del taller ──────────────────────
@router.get("/comisiones", response_model=schemas.ComisionesResponse)
async def ver_comisiones(
    current_user: User = Depends(require_role("taller", "admin")),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "admin":
        from sqlalchemy import select as sa_select
        from app.cotizacion_pagos.models import Cotizacion, Pago
        result = await db.execute(
            sa_select(Cotizacion, Pago).join(Pago, Pago.cotizacion_id == Cotizacion.id)
            .order_by(Pago.created_at.desc())
        )
        rows = result.all()
        items = []
        for cot, pago in rows:
            comision = round(pago.monto * 0.10, 2)
            items.append(schemas.ComisionItem(
                pago_id=pago.id, cotizacion_id=cot.id, incidente_id=cot.incidente_id,
                monto_bruto=round(pago.monto, 2), comision=comision,
                monto_neto=round(pago.monto - comision, 2),
                metodo=pago.metodo, fecha=pago.created_at,
            ))
        bruto = sum(i.monto_bruto for i in items)
        return schemas.ComisionesResponse(
            taller_id=0, total_servicios=len(items),
            ingresos_brutos=round(bruto, 2), tasa_comision=0.10,
            comision_plataforma=round(bruto * 0.10, 2),
            ingresos_netos=round(bruto * 0.90, 2), pagos=items,
        )

    from app.talleres_tecnicos.service import get_taller_by_user
    taller = await get_taller_by_user(current_user.id, db)
    return await service.listar_comisiones(taller.id, db)


# ── CU40 · Crear PaymentIntent Stripe ─────────────────────
@router.post("/stripe/intent", response_model=schemas.StripeIntentResponse)
async def crear_stripe_intent(
    data: schemas.StripeIntentRequest,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    return await service.crear_payment_intent(current_user.id, data.cotizacion_id, db)


# ── CU40 · Confirmar pago Stripe ──────────────────────────
@router.post("/stripe/confirmar", response_model=schemas.PagoResponse)
async def confirmar_stripe(
    data: schemas.StripeConfirmarRequest,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    pago = await service.confirmar_pago_stripe(
        current_user.id, data.cotizacion_id, data.payment_intent_id, db
    )
    from app.reportes.service import log_evento
    await log_evento(db, accion="pago_stripe_confirmado", usuario_id=current_user.id,
                     usuario_nombre=getattr(current_user, 'username', None), entidad="pago",
                     entidad_id=pago.id,
                     detalle={"cotizacion_id": data.cotizacion_id,
                              "payment_intent_id": data.payment_intent_id,
                              "monto": float(pago.monto)})
    return schemas.PagoResponse.model_validate(pago)
