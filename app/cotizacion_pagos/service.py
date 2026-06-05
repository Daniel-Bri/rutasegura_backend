import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException

from app.cotizacion_pagos.models import Cotizacion, Pago
from app.talleres_tecnicos.models import Asignacion
from app.acceso_registro.models import Taller
from app.cotizacion_pagos.schemas import (
    CotizacionCreate, IncidenteDisponibleResponse,
    PagoCreate, ComisionItem, ComisionesResponse,
)

_TASA_COMISION = 0.10  # 10 % plataforma


# ── CU20 · Incidentes disponibles para cotizar ─────────────
async def listar_incidentes_disponibles(taller_id: int, db: AsyncSession) -> list[IncidenteDisponibleResponse]:
    result = await db.execute(
        select(Asignacion).where(
            Asignacion.taller_id == taller_id,
            Asignacion.estado.in_(["invitado", "aceptado"]),
        )
    )
    asignaciones = list(result.scalars().all())

    result = await db.execute(
        select(Cotizacion.incidente_id).where(Cotizacion.taller_id == taller_id)
    )
    ya_cotizados = {row for row in result.scalars().all()}

    return [
        IncidenteDisponibleResponse(
            asignacion_id=a.id,
            incidente_id=a.incidente_id,
            estado_asignacion=a.estado,
            created_at=a.created_at,
        )
        for a in asignaciones
        if a.incidente_id not in ya_cotizados
    ]


# ── CU20 · Generar cotización ──────────────────────────────
async def generar_cotizacion(taller_id: int, data: CotizacionCreate, db: AsyncSession) -> Cotizacion:
    result = await db.execute(
        select(Asignacion).where(
            Asignacion.incidente_id == data.incidente_id,
            Asignacion.taller_id == taller_id,
            Asignacion.estado.in_(["invitado", "aceptado"]),
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="No tienes una invitación activa para este incidente")

    result = await db.execute(
        select(Cotizacion).where(
            Cotizacion.incidente_id == data.incidente_id,
            Cotizacion.taller_id == taller_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya existe una cotización para este incidente")

    monto_total  = sum(item.cantidad * item.precio_unitario for item in data.items)
    detalle_json = json.dumps([item.model_dump() for item in data.items], ensure_ascii=False)

    cotizacion = Cotizacion(
        incidente_id=data.incidente_id,
        taller_id=taller_id,
        monto_estimado=round(monto_total, 2),
        detalle=detalle_json,
    )
    db.add(cotizacion)
    await db.commit()
    await db.refresh(cotizacion)
    return cotizacion


# ── CU20 · Listar cotizaciones del taller ─────────────────
async def listar_cotizaciones(taller_id: int, db: AsyncSession) -> list[Cotizacion]:
    result = await db.execute(
        select(Cotizacion)
        .where(Cotizacion.taller_id == taller_id)
        .order_by(Cotizacion.created_at.desc())
    )
    return list(result.scalars().all())


# ── CU20 · Mis cotizaciones (cliente) ─────────────────────
async def listar_mis_cotizaciones(usuario_id: int, db: AsyncSession) -> list[Cotizacion]:
    from app.emergencias.models import Incidente
    result = await db.execute(
        select(Cotizacion)
        .join(Incidente, Cotizacion.incidente_id == Incidente.id)
        .where(Incidente.usuario_id == usuario_id)
        .order_by(Cotizacion.created_at.desc())
    )
    return list(result.scalars().all())


# ── CU20 · Ver cotización por ID ───────────────────────────
async def get_cotizacion(cotizacion_id: int, db: AsyncSession) -> Cotizacion:
    result = await db.execute(select(Cotizacion).where(Cotizacion.id == cotizacion_id))
    cotizacion = result.scalar_one_or_none()
    if not cotizacion:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    return cotizacion


# ── CU20 · Confirmar / Rechazar cotización ─────────────────
async def actualizar_estado(cotizacion_id: int, nuevo_estado: str, db: AsyncSession) -> Cotizacion:
    cotizacion = await get_cotizacion(cotizacion_id, db)
    if cotizacion.estado != "pendiente":
        raise HTTPException(status_code=400, detail="Solo se pueden confirmar cotizaciones en estado pendiente")

    cotizacion.estado = nuevo_estado

    # Si el cliente ACEPTA: activar la asignación de ese taller y rechazar las otras
    if nuevo_estado == "aceptada":
        # Marcar asignación del taller elegido como "aceptado"
        asig_res = await db.execute(
            select(Asignacion).where(
                Asignacion.incidente_id == cotizacion.incidente_id,
                Asignacion.taller_id == cotizacion.taller_id,
                Asignacion.estado == "invitado",
            )
        )
        asig_elegida = asig_res.scalar_one_or_none()
        if asig_elegida:
            asig_elegida.estado = "aceptado"

        # Rechazar asignaciones de los otros talleres invitados
        otras_res = await db.execute(
            select(Asignacion).where(
                Asignacion.incidente_id == cotizacion.incidente_id,
                Asignacion.taller_id != cotizacion.taller_id,
                Asignacion.estado == "invitado",
            )
        )
        for otra in otras_res.scalars().all():
            otra.estado = "rechazado"

        # Rechazar las cotizaciones de los otros talleres
        otras_cot_res = await db.execute(
            select(Cotizacion).where(
                Cotizacion.incidente_id == cotizacion.incidente_id,
                Cotizacion.taller_id != cotizacion.taller_id,
                Cotizacion.estado == "pendiente",
            )
        )
        for otra_cot in otras_cot_res.scalars().all():
            otra_cot.estado = "rechazada"

        # Notificar al taller elegido
        try:
            from app.acceso_registro.models import Taller as _Taller
            from app.notificaciones.service import notificar_usuario
            from app.comunicacion.websocket import notify
            t_res = await db.execute(
                select(_Taller).where(_Taller.id == cotizacion.taller_id)
            )
            taller = t_res.scalar_one_or_none()
            if taller:
                monto_str = f"Bs. {cotizacion.monto_estimado:.2f}"
                await notificar_usuario(
                    taller.usuario_id,
                    "✅ Cotización aceptada",
                    f"El cliente aceptó tu cotización de {monto_str}. Asigna un técnico para comenzar.",
                    db,
                    {"tipo": "cotizacion_aceptada", "cotizacion_id": str(cotizacion_id)},
                )
                await notify(taller.usuario_id, "notificacion", {
                    "titulo": "✅ Cotización aceptada",
                    "mensaje": f"El cliente aceptó tu cotización de {monto_str}.",
                    "tipo": "cotizacion_aceptada",
                    "referencia_id": cotizacion.incidente_id,
                })
        except Exception:
            pass

    await db.commit()
    await db.refresh(cotizacion)
    return cotizacion


# ── CU20 · Realizar pago (cliente) ────────────────────────
async def realizar_pago(usuario_id: int, data: PagoCreate, db: AsyncSession) -> Pago:
    cotizacion = await get_cotizacion(data.cotizacion_id, db)

    from app.emergencias.models import Incidente
    result = await db.execute(
        select(Incidente).where(
            Incidente.id == cotizacion.incidente_id,
            Incidente.usuario_id == usuario_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="No tienes permiso para pagar esta cotización")

    if cotizacion.estado != "aceptada":
        raise HTTPException(status_code=400, detail="Solo se puede pagar una cotización aceptada")

    # El pago solo se habilita cuando el servicio fue completado
    from app.talleres_tecnicos.models import Asignacion
    asig_res = await db.execute(
        select(Asignacion)
        .where(Asignacion.incidente_id == cotizacion.incidente_id,
               Asignacion.taller_id == cotizacion.taller_id)
        .order_by(Asignacion.created_at.desc())
    )
    asig = asig_res.scalars().first()
    if not asig or asig.estado != "finalizado":
        estado_actual = asig.estado if asig else "sin asignación"
        raise HTTPException(
            status_code=400,
            detail=f"El pago solo está disponible cuando el servicio es finalizado. Estado actual: {estado_actual}",
        )

    existing = await db.execute(select(Pago).where(Pago.cotizacion_id == data.cotizacion_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Esta cotización ya fue pagada")

    pago = Pago(
        cotizacion_id=data.cotizacion_id,
        monto=cotizacion.monto_estimado,
        metodo=data.metodo,
        estado="completado",
    )
    db.add(pago)
    cotizacion.estado = "pagada"
    await db.commit()
    await db.refresh(pago)
    return pago


# ── CU26 · Comisiones del taller ──────────────────────────
async def listar_comisiones(taller_id: int, db: AsyncSession) -> ComisionesResponse:
    result = await db.execute(
        select(Cotizacion, Pago)
        .join(Pago, Pago.cotizacion_id == Cotizacion.id)
        .where(Cotizacion.taller_id == taller_id)
        .order_by(Pago.created_at.desc())
    )
    rows = result.all()

    items: list[ComisionItem] = []
    for cotizacion, pago in rows:
        comision = round(pago.monto * _TASA_COMISION, 2)
        items.append(ComisionItem(
            pago_id=pago.id,
            cotizacion_id=cotizacion.id,
            incidente_id=cotizacion.incidente_id,
            monto_bruto=round(pago.monto, 2),
            comision=comision,
            monto_neto=round(pago.monto - comision, 2),
            metodo=pago.metodo,
            fecha=pago.created_at,
        ))

    bruto = sum(i.monto_bruto for i in items)
    return ComisionesResponse(
        taller_id=taller_id,
        total_servicios=len(items),
        ingresos_brutos=round(bruto, 2),
        tasa_comision=_TASA_COMISION,
        comision_plataforma=round(bruto * _TASA_COMISION, 2),
        ingresos_netos=round(bruto * (1 - _TASA_COMISION), 2),
        pagos=items,
    )


# ── CU40 · Crear PaymentIntent en Stripe ──────────────────
async def crear_payment_intent(usuario_id: int, cotizacion_id: int, db: AsyncSession) -> dict:
    import stripe
    import asyncio
    from app.core.config import settings
    stripe.api_key = settings.STRIPE_SECRET_KEY

    cotizacion = await get_cotizacion(cotizacion_id, db)

    from app.emergencias.models import Incidente
    result = await db.execute(
        select(Incidente).where(
            Incidente.id == cotizacion.incidente_id,
            Incidente.usuario_id == usuario_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="No tienes permiso para pagar esta cotización")

    if cotizacion.estado != "aceptada":
        raise HTTPException(status_code=400, detail="Solo se puede pagar una cotización aceptada")

    from app.talleres_tecnicos.models import Asignacion
    asig_res2 = await db.execute(
        select(Asignacion)
        .where(Asignacion.incidente_id == cotizacion.incidente_id,
               Asignacion.taller_id == cotizacion.taller_id)
        .order_by(Asignacion.created_at.desc())
    )
    asig2 = asig_res2.scalars().first()
    if not asig2 or asig2.estado != "finalizado":
        estado_actual = asig2.estado if asig2 else "sin asignación"
        raise HTTPException(
            status_code=400,
            detail=f"El pago solo está disponible cuando el servicio es finalizado. Estado actual: {estado_actual}",
        )

    existing = await db.execute(select(Pago).where(Pago.cotizacion_id == cotizacion_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Esta cotización ya fue pagada")

    monto_centavos = int(cotizacion.monto_estimado * 100)
    if monto_centavos < 50:
        raise HTTPException(status_code=400, detail="El monto mínimo para pago con tarjeta es Bs. 0.50")

    try:
        intent = await asyncio.to_thread(
            lambda: stripe.PaymentIntent.create(
                amount=monto_centavos,
                currency="usd",
                metadata={"cotizacion_id": str(cotizacion_id), "usuario_id": str(usuario_id)},
            )
        )
    except stripe.error.AuthenticationError:
        raise HTTPException(status_code=500, detail="Error de autenticación con Stripe. Verifica STRIPE_SECRET_KEY en .env")
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Error de Stripe: {getattr(e, 'user_message', None) or str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al crear el pago: {str(e)}")

    return {
        "client_secret": intent.client_secret,
        "payment_intent_id": intent.id,
        "monto": cotizacion.monto_estimado,
        "currency": "usd",
    }


# ── CU40 · Confirmar pago Stripe y guardar en BD ──────────
async def confirmar_pago_stripe(
    usuario_id: int, cotizacion_id: int, payment_intent_id: str, db: AsyncSession
) -> Pago:
    import stripe
    import asyncio
    from app.core.config import settings
    stripe.api_key = settings.STRIPE_SECRET_KEY

    intent = await asyncio.to_thread(stripe.PaymentIntent.retrieve, payment_intent_id)

    if intent.status != "succeeded":
        raise HTTPException(
            status_code=400,
            detail=f"El pago no fue completado en Stripe. Estado: {intent.status}",
        )

    cotizacion = await get_cotizacion(cotizacion_id, db)

    existing = await db.execute(select(Pago).where(Pago.cotizacion_id == cotizacion_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Esta cotización ya fue pagada")

    pago = Pago(
        cotizacion_id=cotizacion_id,
        monto=cotizacion.monto_estimado,
        metodo="tarjeta",
        estado="completado",
        stripe_payment_intent_id=payment_intent_id,
    )
    db.add(pago)
    cotizacion.estado = "pagada"
    await db.commit()
    await db.refresh(pago)
    return pago

