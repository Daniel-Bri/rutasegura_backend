import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException, status

from app.acceso_registro.models import User, Vehiculo, Taller, Tenant, PasswordResetCode
from app.acceso_registro.schemas import UserCreate, UserLogin, VehiculoCreate, TallerCreate, UserUpdate
from app.core.security import hash_password, verify_password, create_access_token


# ── Autenticación ──────────────────────────────────────────
async def registrar_usuario(data: UserCreate, db: AsyncSession) -> tuple[str, User]:
    result = await db.execute(select(User).where(User.email == data.email.lower()))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El username ya está en uso")

    user = User(
        email=data.email.lower(),
        username=data.username,
        full_name=data.full_name,
        telefono=data.telefono,
        hashed_password=hash_password(data.password),
        role="cliente",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role})
    return token, user


async def iniciar_sesion(data: UserLogin, db: AsyncSession) -> tuple[str, User]:
    result = await db.execute(select(User).where(User.email == data.email.lower()))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Cuenta desactivada")

    if user.tenant_id:
        result_t = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
        tenant = result_t.scalar_one_or_none()
        if tenant and not tenant.activo:
            raise HTTPException(status_code=403, detail="Tu organización está desactivada. Contacta al administrador.")

    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role})
    return token, user


async def cambiar_contrasena(user: User, current_password: str, new_password: str, db: AsyncSession) -> None:
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")
    user.hashed_password = hash_password(new_password)
    await db.commit()


async def solicitar_reset_contrasena(email: str, db: AsyncSession) -> None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No existe una cuenta con ese correo")

    # Invalidar códigos anteriores no usados
    old = await db.execute(
        select(PasswordResetCode).where(
            PasswordResetCode.email == email.lower(),
            PasswordResetCode.used.is_(False),
        )
    )
    for c in old.scalars():
        c.used = True

    code = "".join(str(random.randint(0, 9)) for _ in range(6))
    reset = PasswordResetCode(
        email=email.lower(),
        code=code,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(reset)
    await db.commit()

    from app.core.email_service import send_reset_code
    await send_reset_code(email, code, user.full_name or user.username)


async def resetear_contrasena(email: str, code: str, new_password: str, db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(PasswordResetCode).where(
            PasswordResetCode.email == email.lower(),
            PasswordResetCode.code == code,
            PasswordResetCode.used.is_(False),
            PasswordResetCode.expires_at > now,
        )
    )
    reset = result.scalar_one_or_none()
    if not reset:
        raise HTTPException(status_code=400, detail="Código inválido o expirado")

    reset.used = True

    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()
    user.hashed_password = hash_password(new_password)
    await db.commit()


# ── CU03 / CU04 - Vehículos ────────────────────────────────
async def crear_vehiculo(data: VehiculoCreate, user: User, db: AsyncSession) -> Vehiculo:
    result = await db.execute(select(Vehiculo).where(Vehiculo.placa == data.placa))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="La placa ya está registrada en el sistema")

    vehiculo = Vehiculo(
        usuario_id=user.id,
        placa=data.placa,
        marca=data.marca,
        modelo=data.modelo,
        anio=data.anio,
        color=data.color,
        numero_seguro=data.numero_seguro,
    )
    db.add(vehiculo)
    await db.commit()
    await db.refresh(vehiculo)
    return vehiculo


async def listar_vehiculos_usuario(usuario_id: int, db: AsyncSession) -> list[Vehiculo]:
    result = await db.execute(
        select(Vehiculo).where(Vehiculo.usuario_id == usuario_id, Vehiculo.activo == True)
    )
    return list(result.scalars().all())


async def eliminar_vehiculo(vehiculo_id: int, usuario_id: int, db: AsyncSession) -> None:
    result = await db.execute(
        select(Vehiculo).where(Vehiculo.id == vehiculo_id, Vehiculo.usuario_id == usuario_id)
    )
    vehiculo = result.scalar_one_or_none()
    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    vehiculo.activo = False
    await db.commit()


# ── CU12 - Taller ──────────────────────────────────────────
async def crear_taller(data: TallerCreate, user: User, db: AsyncSession) -> Taller:
    result = await db.execute(select(Taller).where(Taller.usuario_id == user.id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya tienes un taller registrado")

    import json as _json
    especialidades_json = _json.dumps(data.especialidades) if data.especialidades else None

    taller = Taller(
        usuario_id=user.id,
        nombre=data.nombre,
        direccion=data.direccion,
        telefono=data.telefono,
        email_comercial=data.email_comercial,
        latitud=data.latitud,
        longitud=data.longitud,
        especialidades=especialidades_json,
    )
    db.add(taller)
    user.role = "taller"
    await db.commit()
    await db.refresh(taller)
    return taller


async def listar_talleres(estado: str | None, db: AsyncSession) -> list[Taller]:
    query = select(Taller)
    if estado:
        query = query.where(Taller.estado == estado)
    result = await db.execute(query)
    return list(result.scalars().all())


async def cambiar_estado_taller(taller_id: int, nuevo_estado: str, db: AsyncSession) -> Taller:
    result = await db.execute(select(Taller).where(Taller.id == taller_id))
    taller = result.scalar_one_or_none()
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    taller.estado = nuevo_estado
    await db.commit()
    await db.refresh(taller)
    return taller


# ── CU27 - Gestionar usuarios ──────────────────────────────
async def listar_usuarios(
    db: AsyncSession,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[User], int]:
    query = select(User)
    if role:
        query = query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if search:
        s = f"%{search.lower()}%"
        from sqlalchemy import or_
        query = query.where(
            or_(User.email.ilike(s), User.username.ilike(s), User.full_name.ilike(s))
        )

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar_one()

    query = query.order_by(User.created_at.desc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def obtener_usuario(user_id: int, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


async def actualizar_usuario(
    user_id: int, data: UserUpdate, current_admin_id: int, db: AsyncSession
) -> User:
    user = await obtener_usuario(user_id, db)

    if data.email and data.email != user.email:
        existing = await db.execute(select(User).where(User.email == data.email, User.id != user_id))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="El correo ya está en uso por otro usuario")
        user.email = data.email

    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.telefono is not None:
        user.telefono = data.telefono
    if data.role is not None:
        user.role = data.role

    await db.commit()
    await db.refresh(user)
    return user


async def toggle_usuario_activo(
    user_id: int, activar: bool, current_admin_id: int, db: AsyncSession
) -> User:
    if user_id == current_admin_id:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta")
    user = await obtener_usuario(user_id, db)
    user.is_active = activar
    await db.commit()
    await db.refresh(user)
    return user


