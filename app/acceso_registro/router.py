import math
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException
from sqlalchemy import select, update
from app.db.session import get_db
from app.acceso_registro import schemas, service
from app.acceso_registro.schemas import UserResponse, VehiculoResponse, TallerResponse, UserListResponse
from app.core.dependencies import get_current_user, require_role
from app.acceso_registro.models import User, Tenant, Taller

router = APIRouter()


# ── CU01 - Registrarse ─────────────────────────────────────
@router.post("/register", response_model=schemas.Token, status_code=status.HTTP_201_CREATED)
async def register(data: schemas.UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    token, user = await service.registrar_usuario(data, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="register", usuario_id=user.id,
                     usuario_nombre=user.username, entidad="User", entidad_id=user.id,
                     ip=request.client.host if request.client else None)
    return schemas.Token(access_token=token, user=UserResponse.model_validate(user))


# ── CU02 - Iniciar sesión ──────────────────────────────────
@router.post("/login", response_model=schemas.Token)
async def login(data: schemas.UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    token, user = await service.iniciar_sesion(data, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="login", usuario_id=user.id,
                     usuario_nombre=user.username, entidad="User", entidad_id=user.id,
                     ip=request.client.host if request.client else None)
    return schemas.Token(access_token=token, user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    data: schemas.ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.cambiar_contrasena(current_user, data.current_password, data.new_password, db)
    return {"msg": "Contraseña actualizada correctamente"}


@router.post("/request-reset", status_code=status.HTTP_200_OK)
async def request_reset(
    data: schemas.RequestResetRequest,
    db: AsyncSession = Depends(get_db),
):
    await service.solicitar_reset_contrasena(data.email, db)
    return {"msg": "Código enviado al correo registrado"}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    data: schemas.ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    await service.resetear_contrasena(data.email, data.code, data.new_password, db)
    return {"msg": "Contraseña restablecida correctamente"}


# ── CU03 - Registrar vehículo ──────────────────────────────
@router.post("/vehiculos", response_model=VehiculoResponse, status_code=status.HTTP_201_CREATED)
async def registrar_vehiculo(
    data: schemas.VehiculoCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehiculo = await service.crear_vehiculo(data, current_user, db)
    return VehiculoResponse.model_validate(vehiculo)


# ── CU04 - Listar vehículos ────────────────────────────────
@router.get("/vehiculos", response_model=list[VehiculoResponse])
async def listar_vehiculos(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehiculos = await service.listar_vehiculos_usuario(current_user.id, db)
    return [VehiculoResponse.model_validate(v) for v in vehiculos]


@router.delete("/vehiculos/{vehiculo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_vehiculo(
    vehiculo_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.eliminar_vehiculo(vehiculo_id, current_user.id, db)


# ── CU12 - Registrar taller ────────────────────────────────
@router.post("/talleres", response_model=TallerResponse, status_code=status.HTTP_201_CREATED)
async def registrar_taller(
    data: schemas.TallerCreate,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.crear_taller(data, current_user, db)
    return TallerResponse.model_validate(taller)


# ── Mi perfil de taller (taller ve y edita sus propios datos) ─
@router.get("/mi-taller", response_model=TallerResponse)
async def mi_taller_perfil(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select as _sel
    r = await db.execute(_sel(Taller).where(Taller.usuario_id == current_user.id))
    taller = r.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=404, detail="No tienes un taller registrado")
    return TallerResponse.model_validate(taller)


@router.patch("/mi-taller", response_model=TallerResponse)
async def actualizar_mi_taller(
    data: schemas.TallerUpdate,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    import json as _json
    from sqlalchemy import select as _sel
    r = await db.execute(_sel(Taller).where(Taller.usuario_id == current_user.id))
    taller = r.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=404, detail="No tienes un taller registrado")
    if data.nombre is not None:          taller.nombre          = data.nombre
    if data.direccion is not None:       taller.direccion       = data.direccion
    if data.telefono is not None:        taller.telefono        = data.telefono
    if data.email_comercial is not None: taller.email_comercial = data.email_comercial
    if data.latitud is not None:         taller.latitud         = data.latitud
    if data.longitud is not None:        taller.longitud        = data.longitud
    if data.especialidades is not None:  taller.especialidades  = _json.dumps(data.especialidades)
    await db.commit()
    await db.refresh(taller)
    return TallerResponse.model_validate(taller)


# ── CU27 - Listar usuarios (admin) ────────────────────────
@router.get("/usuarios", response_model=UserListResponse)
async def listar_usuarios(
    role: Optional[str] = Query(None),
    activo: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    usuarios, total = await service.listar_usuarios(db, role, activo, search, page, size)
    pages = math.ceil(total / size) if total > 0 else 1
    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in usuarios],
        total=total, page=page, size=size, pages=pages,
    )


@router.get("/usuarios/{user_id}", response_model=UserResponse)
async def obtener_usuario(
    user_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    user = await service.obtener_usuario(user_id, db)
    return UserResponse.model_validate(user)


@router.patch("/usuarios/{user_id}", response_model=UserResponse)
async def actualizar_usuario(
    user_id: int,
    data: schemas.UserUpdate,
    request: Request,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    before = await service.obtener_usuario(user_id, db)
    before_data = {"email": before.email, "full_name": before.full_name,
                   "telefono": before.telefono, "role": before.role}
    user = await service.actualizar_usuario(user_id, data, current_user.id, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="update_user", usuario_id=current_user.id,
                     usuario_nombre=current_user.username, entidad="User", entidad_id=user_id,
                     detalle={"antes": before_data, "despues": data.model_dump(exclude_none=True)},
                     ip=request.client.host if request.client else None)
    return UserResponse.model_validate(user)


@router.patch("/usuarios/{user_id}/activar", response_model=UserResponse)
async def activar_usuario(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    user = await service.toggle_usuario_activo(user_id, True, current_user.id, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="activate_user", usuario_id=current_user.id,
                     usuario_nombre=current_user.username, entidad="User", entidad_id=user_id,
                     ip=request.client.host if request.client else None)
    return UserResponse.model_validate(user)


@router.patch("/usuarios/{user_id}/desactivar", response_model=UserResponse)
async def desactivar_usuario(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    user = await service.toggle_usuario_activo(user_id, False, current_user.id, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="deactivate_user", usuario_id=current_user.id,
                     usuario_nombre=current_user.username, entidad="User", entidad_id=user_id,
                     ip=request.client.host if request.client else None)
    return UserResponse.model_validate(user)


# ── CU34 - Aprobar / rechazar taller ──────────────────────
@router.get("/talleres", response_model=list[TallerResponse])
async def listar_talleres(
    estado: Optional[str] = None,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    talleres = await service.listar_talleres(estado, db)
    # Poblar tenant_nombre
    tenant_ids = {t.tenant_id for t in talleres if t.tenant_id}
    tenants_map: dict[int, str] = {}
    if tenant_ids:
        from app.acceso_registro.models import Tenant
        ten_res = await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
        for ten in ten_res.scalars().all():
            tenants_map[ten.id] = ten.nombre
    result = []
    for t in talleres:
        r = TallerResponse.model_validate(t)
        r.tenant_nombre = tenants_map.get(t.tenant_id) if t.tenant_id else None
        result.append(r)
    return result


class AprobarPayload(BaseModel):
    tenant_id: Optional[int] = None


@router.patch("/talleres/{taller_id}/aprobar", response_model=TallerResponse)
async def aprobar_taller(
    taller_id: int,
    request: Request,
    body: AprobarPayload = AprobarPayload(),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.cambiar_estado_taller(taller_id, "aprobado", db)
    # Asignar tenant si se especificó
    if body.tenant_id is not None:
        taller.tenant_id = body.tenant_id
        await db.commit()
        await db.refresh(taller)
    from app.reportes.service import log_evento
    await log_evento(db, accion="approve_taller", usuario_id=current_user.id,
                     usuario_nombre=current_user.username, entidad="Taller", entidad_id=taller_id,
                     ip=request.client.host if request.client else None)
    r = TallerResponse.model_validate(taller)
    if taller.tenant_id:
        from app.acceso_registro.models import Tenant
        ten_res = await db.execute(select(Tenant).where(Tenant.id == taller.tenant_id))
        ten = ten_res.scalar_one_or_none()
        r.tenant_nombre = ten.nombre if ten else None
    return r


@router.patch("/talleres/{taller_id}/rechazar", response_model=TallerResponse)
async def rechazar_taller(
    taller_id: int,
    request: Request,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    taller = await service.cambiar_estado_taller(taller_id, "rechazado", db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="reject_taller", usuario_id=current_user.id,
                     usuario_nombre=current_user.username, entidad="Taller", entidad_id=taller_id,
                     ip=request.client.host if request.client else None)
    return TallerResponse.model_validate(taller)


# ── CU42 - Gestionar Tenants ──────────────────────────────

@router.post("/tenants", response_model=schemas.TenantResponse, status_code=status.HTTP_201_CREATED)
async def crear_tenant(
    data: schemas.TenantCreate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    existe = await db.execute(select(Tenant).where(Tenant.slug == data.slug))
    if existe.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya existe un tenant con ese slug")
    tenant = Tenant(
        nombre=data.nombre,
        slug=data.slug,
        descripcion=data.descripcion,
        config=data.config,
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return schemas.TenantResponse.model_validate(tenant)


@router.get("/tenants", response_model=list[schemas.TenantResponse])
async def listar_tenants(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tenant).order_by(Tenant.id))
    return [schemas.TenantResponse.model_validate(t) for t in result.scalars().all()]


@router.get("/tenants/{tenant_id}", response_model=schemas.TenantDetalle)
async def detalle_tenant(
    tenant_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    usuarios_r = await db.execute(select(User).where(User.tenant_id == tenant_id))
    talleres_r = await db.execute(select(Taller).where(Taller.tenant_id == tenant_id))

    detalle = schemas.TenantDetalle.model_validate(tenant)
    detalle.usuarios = [schemas.UserResponse.model_validate(u) for u in usuarios_r.scalars().all()]
    detalle.talleres = [schemas.TallerResponse.model_validate(t) for t in talleres_r.scalars().all()]
    return detalle


@router.patch("/tenants/{tenant_id}", response_model=schemas.TenantResponse)
async def actualizar_tenant(
    tenant_id: int,
    data: schemas.TenantUpdate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(tenant, field, value)
    await db.commit()
    await db.refresh(tenant)
    return schemas.TenantResponse.model_validate(tenant)


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desactivar_tenant(
    tenant_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    if tenant.slug == "default":
        raise HTTPException(status_code=400, detail="No se puede eliminar el tenant por defecto")
    tenant.activo = False
    await db.commit()


@router.post("/tenants/{tenant_id}/usuarios", response_model=schemas.UserResponse)
async def asignar_usuario_tenant(
    tenant_id: int,
    data: schemas.AsignarUsuarioRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    tenant_r = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant_r.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    user_r = await db.execute(select(User).where(User.id == data.usuario_id))
    user = user_r.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    user.tenant_id = tenant_id
    await db.commit()
    await db.refresh(user)
    return schemas.UserResponse.model_validate(user)


@router.delete("/tenants/{tenant_id}/usuarios/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_usuario_tenant(
    tenant_id: int,
    usuario_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    user_r = await db.execute(select(User).where(User.id == usuario_id, User.tenant_id == tenant_id))
    user = user_r.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no pertenece a este tenant")
    user.tenant_id = None
    await db.commit()


@router.post("/tenants/{tenant_id}/talleres", response_model=schemas.TallerResponse)
async def asignar_taller_tenant(
    tenant_id: int,
    data: schemas.AsignarTallerRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    tenant_r = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant_r.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    taller_r = await db.execute(select(Taller).where(Taller.id == data.taller_id))
    taller = taller_r.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    taller.tenant_id = tenant_id
    await db.commit()
    await db.refresh(taller)
    return schemas.TallerResponse.model_validate(taller)


@router.delete("/tenants/{tenant_id}/talleres/{taller_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_taller_tenant(
    tenant_id: int,
    taller_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    taller_r = await db.execute(select(Taller).where(Taller.id == taller_id, Taller.tenant_id == tenant_id))
    taller = taller_r.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no pertenece a este tenant")
    taller.tenant_id = None
    await db.commit()
