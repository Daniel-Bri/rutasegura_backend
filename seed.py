"""
Carga datos iniciales en la base de datos.
Uso: python seed.py
"""
import asyncio
import json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from dotenv import load_dotenv
import os

load_dotenv()

from app.acceso_registro.models import User, Vehiculo, Taller, Tenant
from app.emergencias.models import Incidente
from app.talleres_tecnicos.models import Tecnico, Asignacion, ServicioRealizado
from app.cotizacion_pagos.models import Cotizacion
from app.db.base import Base
from app.core.security import hash_password

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    print("[seed] DATABASE_URL no configurada — omitiendo seed.")
    raise SystemExit(0)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, connect_args={"timeout": 5})
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ── Usuarios ─────────────────────────────────────────────────────────────────
USUARIOS = [
    {"email": "admin@rutasegura.com",    "username": "admin",    "full_name": "Administrador",          "password": "12345678", "role": "admin"},
    {"email": "carlos@gmail.com",        "username": "carlos",   "full_name": "Carlos Mendoza",         "password": "12345678", "role": "cliente"},
    {"email": "ana@gmail.com",           "username": "ana",      "full_name": "Ana Quispe",             "password": "12345678", "role": "cliente"},
    {"email": "autofix@gmail.com",       "username": "autofix",  "full_name": "AutoFix Express SCZ",    "password": "12345678", "role": "taller"},
    {"email": "mecanica_alemana@gmail.com","username": "malemana","full_name": "Mecánica Alemana",       "password": "12345678", "role": "taller"},
    {"email": "equipetrol_autos@gmail.com","username": "equipeautos","full_name": "Equipetrol Autos",   "password": "12345678", "role": "taller"},
    {"email": "motores_norte@gmail.com", "username": "mtnorte",  "full_name": "Motores del Norte",      "password": "12345678", "role": "taller"},
    {"email": "luis@gmail.com",          "username": "luis",     "full_name": "Luis Vargas",            "password": "12345678", "role": "tecnico"},
    {"email": "pedro@gmail.com",         "username": "pedro",    "full_name": "Pedro Huanca",           "password": "12345678", "role": "tecnico"},
]

async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:

        # ── 0. Tenants ────────────────────────────────────────────────────────
        print("\n[0/8] Tenants...")
        TENANTS = [
            {
                "nombre": "Plataforma RutaSegura",
                "slug": "default",
                "descripcion": "Tenant principal de la plataforma",
                "activo": True,
            },
            {
                "nombre": "AutoFix Red SCZ",
                "slug": "autofix_scz",
                "descripcion": "Red de talleres AutoFix en Santa Cruz de la Sierra",
                "activo": True,
            },
            {
                "nombre": "Demo Corp",
                "slug": "demo",
                "descripcion": "Tenant de demostración",
                "activo": False,
            },
        ]
        tenants_map: dict[str, Tenant] = {}
        for data in TENANTS:
            result = await db.execute(select(Tenant).where(Tenant.slug == data["slug"]))
            ten = result.scalar_one_or_none()
            if ten:
                print(f"  [skip] {data['slug']}")
            else:
                ten = Tenant(
                    nombre=data["nombre"],
                    slug=data["slug"],
                    descripcion=data["descripcion"],
                    activo=data["activo"],
                )
                db.add(ten)
                await db.flush()
                print(f"  [ok]   {data['slug']} — {data['nombre']}")
            tenants_map[data["slug"]] = ten
        await db.commit()
        for k in tenants_map:
            await db.refresh(tenants_map[k])

        default_tenant = tenants_map["default"]
        autofix_tenant = tenants_map["autofix_scz"]

        # ── 1. Usuarios ───────────────────────────────────────────────────────
        print("\n[1/8] Usuarios...")
        tenant_por_usuario = {
            "admin":      None,
            "carlos":     default_tenant,
            "ana":        default_tenant,
            "autofix":    autofix_tenant,
            "malemana":   default_tenant,
            "equipeautos":default_tenant,
            "mtnorte":    default_tenant,
            "luis":       autofix_tenant,
            "pedro":      autofix_tenant,
        }
        users: dict[str, User] = {}
        for data in USUARIOS:
            result = await db.execute(select(User).where(User.email == data["email"]))
            u = result.scalar_one_or_none()
            if u:
                print(f"  [skip] {data['email']}")
            else:
                ten = tenant_por_usuario.get(data["username"])
                u = User(
                    email=data["email"],
                    username=data["username"],
                    full_name=data["full_name"],
                    hashed_password=hash_password(data["password"]),
                    role=data["role"],
                    tenant_id=ten.id if ten else None,
                )
                db.add(u)
                await db.flush()
                tenant_label = ten.slug if ten else "sin tenant"
                print(f"  [ok]   {data['email']} → tenant: {tenant_label}")
            users[data["username"]] = u
        await db.commit()
        for key in users:
            await db.refresh(users[key])

        # ── 2. Vehículos ──────────────────────────────────────────────────────
        print("\n[2/8] Vehículos...")
        VEHICULOS = [
            {"usuario": "carlos", "placa": "1234SCZ", "marca": "Toyota",   "modelo": "Hilux",     "anio": 2020, "color": "Blanco"},
            {"usuario": "carlos", "placa": "5678SCZ", "marca": "Honda",    "modelo": "HR-V",      "anio": 2022, "color": "Negro"},
            {"usuario": "ana",    "placa": "9012SCZ", "marca": "Hyundai",  "modelo": "Tucson",    "anio": 2019, "color": "Plata"},
            {"usuario": "ana",    "placa": "3456SCZ", "marca": "Kia",      "modelo": "Sportage",  "anio": 2021, "color": "Rojo"},
        ]
        vehiculos: dict[str, Vehiculo] = {}
        for v in VEHICULOS:
            result = await db.execute(select(Vehiculo).where(Vehiculo.placa == v["placa"]))
            veh = result.scalar_one_or_none()
            if veh:
                print(f"  [skip] {v['placa']}")
            else:
                veh = Vehiculo(
                    usuario_id=users[v["usuario"]].id,
                    placa=v["placa"],
                    marca=v["marca"],
                    modelo=v["modelo"],
                    anio=v["anio"],
                    color=v["color"],
                )
                db.add(veh)
                await db.flush()
                print(f"  [ok]   {v['placa']} ({v['marca']} {v['modelo']})")
            vehiculos[v["placa"]] = veh
        await db.commit()
        for k in vehiculos:
            await db.refresh(vehiculos[k])

        # ── 3. Talleres (todos en Santa Cruz de la Sierra) ────────────────────
        print("\n[3/8] Talleres...")
        TALLERES = [
            {
                "usuario": "autofix",
                "nombre": "AutoFix Express SCZ",
                "direccion": "Av. Alemana 2do Anillo Nro. 542, Santa Cruz de la Sierra",
                "telefono": "33412567",
                "email_comercial": "autofix@gmail.com",
                "latitud": -17.7722, "longitud": -63.2081,
                "estado": "aprobado", "disponible": True, "rating": 4.7,
                "tenant": autofix_tenant,
            },
            {
                "usuario": "malemana",
                "nombre": "Mecánica Alemana",
                "direccion": "Av. Cañoto Nro. 1180 esq. Beni, Santa Cruz de la Sierra",
                "telefono": "33456789",
                "email_comercial": "mecanica_alemana@gmail.com",
                "latitud": -17.7845, "longitud": -63.1812,
                "estado": "aprobado", "disponible": True, "rating": 4.3,
                "tenant": default_tenant,
            },
            {
                "usuario": "equipeautos",
                "nombre": "Equipetrol Autos",
                "direccion": "Av. San Martín 3er Anillo Nro. 875, Barrio Equipetrol, Santa Cruz",
                "telefono": "76123456",
                "email_comercial": "equipetrol_autos@gmail.com",
                "latitud": -17.7681, "longitud": -63.2147,
                "estado": "aprobado", "disponible": False, "rating": 3.9,
                "tenant": default_tenant,
            },
            {
                "usuario": "mtnorte",
                "nombre": "Motores del Norte",
                "direccion": "Av. Virgen de Cotoca 4to Anillo Nro. 2340, Plan 3000, Santa Cruz",
                "telefono": "78901234",
                "email_comercial": "motores_norte@gmail.com",
                "latitud": -17.8195, "longitud": -63.1095,
                "estado": "pendiente", "disponible": False, "rating": 0.0,
                "tenant": default_tenant,
            },
        ]
        talleres: dict[str, Taller] = {}
        for t in TALLERES:
            result = await db.execute(select(Taller).where(Taller.usuario_id == users[t["usuario"]].id))
            tal = result.scalar_one_or_none()
            if tal:
                print(f"  [skip] {t['nombre']}")
            else:
                tal = Taller(
                    usuario_id=users[t["usuario"]].id,
                    nombre=t["nombre"],
                    direccion=t["direccion"],
                    telefono=t["telefono"],
                    email_comercial=t["email_comercial"],
                    latitud=t["latitud"],
                    longitud=t["longitud"],
                    estado=t["estado"],
                    disponible=t["disponible"],
                    rating=t["rating"],
                    tenant_id=t["tenant"].id,
                )
                db.add(tal)
                await db.flush()
                print(f"  [ok]   {t['nombre']} ({t['estado']}) → {t['latitud']}, {t['longitud']}")
            talleres[t["usuario"]] = tal
        await db.commit()
        for k in talleres:
            await db.refresh(talleres[k])

        # ── 4. Técnicos (del taller principal: AutoFix) ───────────────────────
        print("\n[4/8] Técnicos...")
        taller_principal = talleres["autofix"]
        TECNICOS = [
            {"nombre": "Luis Vargas",  "especialidad": "Motor y transmisión",  "telefono": "71111111", "estado": "ocupado",    "usuario": "luis"},
            {"nombre": "Pedro Huanca", "especialidad": "Eléctrica automotriz", "telefono": "72222222", "estado": "ocupado",    "usuario": "pedro"},
            {"nombre": "Jorge Mamani", "especialidad": "Frenos y suspensión",  "telefono": "73333333", "estado": "disponible", "usuario": None},
            {"nombre": "Rosa Chávez",  "especialidad": "Carrocería y pintura", "telefono": "74444444", "estado": "disponible", "usuario": None},
            {"nombre": "Mario Quispe", "especialidad": "Diagnóstico OBD",      "telefono": "75555555", "estado": "inactivo",   "usuario": None},
        ]
        tecnicos: list[Tecnico] = []
        for t in TECNICOS:
            result = await db.execute(
                select(Tecnico).where(
                    Tecnico.taller_id == taller_principal.id,
                    Tecnico.nombre == t["nombre"],
                )
            )
            tec = result.scalar_one_or_none()
            if tec:
                print(f"  [skip] {t['nombre']}")
            else:
                usuario_id = users[t["usuario"]].id if t["usuario"] else None
                tec = Tecnico(
                    taller_id=taller_principal.id,
                    usuario_id=usuario_id,
                    nombre=t["nombre"],
                    especialidad=t["especialidad"],
                    telefono=t["telefono"],
                    estado=t["estado"],
                    activo=t["estado"] != "inactivo",
                )
                db.add(tec)
                await db.flush()
                print(f"  [ok]   {t['nombre']} ({t['estado']})")
            tecnicos.append(tec)
        await db.commit()
        for tec in tecnicos:
            await db.refresh(tec)

        # ── 5. Incidentes (ubicados en Santa Cruz) ────────────────────────────
        print("\n[5/8] Incidentes...")
        INCIDENTES = [
            # resueltos → historial
            {"usuario": "carlos", "placa": "1234SCZ", "lat": -17.7730, "lon": -63.2090, "descripcion": "Vehículo no enciende, batería descargada",               "estado": "resuelto",   "prioridad": "alta"},
            {"usuario": "ana",    "placa": "9012SCZ", "lat": -17.7850, "lon": -63.1820, "descripcion": "Pinchazo de llanta delantera derecha",                   "estado": "resuelto",   "prioridad": "media"},
            # en_proceso → asignaciones activas
            {"usuario": "carlos", "placa": "5678SCZ", "lat": -17.7900, "lon": -63.1750, "descripcion": "Fuga de aceite por el carter, humo blanco",              "estado": "en_proceso", "prioridad": "alta"},
            {"usuario": "ana",    "placa": "3456SCZ", "lat": -17.7680, "lon": -63.2150, "descripcion": "Frenos no responden correctamente al frenar",            "estado": "en_proceso", "prioridad": "alta"},
            {"usuario": "carlos", "placa": "1234SCZ", "lat": -17.7760, "lon": -63.2010, "descripcion": "Recalentamiento del motor, temperatura muy alta",        "estado": "en_proceso", "prioridad": "alta"},
            {"usuario": "ana",    "placa": "9012SCZ", "lat": -17.8100, "lon": -63.1600, "descripcion": "Ruido extraño al acelerar, posible problema en transmisión", "estado": "en_proceso", "prioridad": "media"},
            # pendiente → sin asignación
            {"usuario": "carlos", "placa": "5678SCZ", "lat": -17.7620, "lon": -63.1980, "descripcion": "Luces del tablero parpadeando, posible falla eléctrica",  "estado": "pendiente",  "prioridad": "baja"},
            # en_proceso → para cotización
            {"usuario": "ana",    "placa": "3456SCZ", "lat": -17.7950, "lon": -63.1700, "descripcion": "Cambio de aceite y revisión general preventiva",         "estado": "en_proceso", "prioridad": "baja"},
        ]
        incidentes: list[Incidente] = []
        for inc_data in INCIDENTES:
            inc = Incidente(
                usuario_id=users[inc_data["usuario"]].id,
                vehiculo_id=vehiculos[inc_data["placa"]].id,
                latitud=inc_data["lat"],
                longitud=inc_data["lon"],
                descripcion=inc_data["descripcion"],
                estado=inc_data["estado"],
                prioridad=inc_data["prioridad"],
            )
            db.add(inc)
            await db.flush()
            incidentes.append(inc)
            print(f"  [ok]   Incidente #{inc.id} ({inc_data['prioridad']}) - {inc_data['descripcion'][:50]}...")
        await db.commit()
        for inc in incidentes:
            await db.refresh(inc)

        # ── 6. Asignaciones ───────────────────────────────────────────────────
        print("\n[6/8] Asignaciones...")
        tec_luis  = tecnicos[0]
        tec_pedro = tecnicos[1]

        ASIGNACIONES = [
            {"incidente": incidentes[0], "tecnico": tec_luis,  "estado": "finalizado",    "eta": None, "obs": "Servicio completado exitosamente"},
            {"incidente": incidentes[1], "tecnico": tec_pedro, "estado": "finalizado",    "eta": None, "obs": "Llanta cambiada sin inconvenientes"},
            {"incidente": incidentes[2], "tecnico": tec_luis,  "estado": "en_reparacion", "eta": 30,   "obs": "Diagnóstico completado, en proceso de reparación"},
            {"incidente": incidentes[3], "tecnico": tec_pedro, "estado": "en_reparacion", "eta": 45,   "obs": "Revisando sistema de frenos"},
            {"incidente": incidentes[4], "tecnico": tec_luis,  "estado": "en_camino",     "eta": 15,   "obs": "Técnico en camino al lugar"},
            {"incidente": incidentes[5], "tecnico": None,      "estado": "aceptado",      "eta": None, "obs": None},
            {"incidente": incidentes[6], "tecnico": tec_pedro, "estado": "en_sitio",      "eta": 0,    "obs": "Técnico en el lugar evaluando"},
            {"incidente": incidentes[7], "tecnico": None,      "estado": "aceptado",      "eta": None, "obs": None},
        ]
        asignaciones: list[Asignacion] = []
        for a in ASIGNACIONES:
            asig = Asignacion(
                incidente_id=a["incidente"].id,
                taller_id=taller_principal.id,
                tecnico_id=a["tecnico"].id if a["tecnico"] else None,
                estado=a["estado"],
                eta=a["eta"],
                observacion=a["obs"],
            )
            db.add(asig)
            await db.flush()
            asignaciones.append(asig)
            tec_nombre = a["tecnico"].nombre if a["tecnico"] else "Sin técnico"
            print(f"  [ok]   Asignación #{asig.id} ({a['estado']}) → {tec_nombre}")
        await db.commit()
        for asig in asignaciones:
            await db.refresh(asig)

        # ── 7. Servicios realizados ───────────────────────────────────────────
        print("\n[7/8] Servicios realizados...")
        SERVICIOS = [
            {
                "asignacion": asignaciones[0],
                "descripcion": "Se realizó carga completa de batería y revisión del sistema eléctrico. Se verificó alternador y cableado.",
                "repuestos": json.dumps([
                    {"descripcion": "Batería 12V 60Ah",     "cantidad": 1},
                    {"descripcion": "Terminales de batería", "cantidad": 2},
                ]),
                "observaciones": "Se recomienda revisión eléctrica completa en 6 meses.",
            },
            {
                "asignacion": asignaciones[1],
                "descripcion": "Cambio de llanta delantera derecha por pinchazo. Se revisaron las demás llantas y se ajustó presión.",
                "repuestos": json.dumps([
                    {"descripcion": "Llanta 195/65 R15", "cantidad": 1},
                    {"descripcion": "Parche vulcanizado", "cantidad": 1},
                ]),
                "observaciones": "Las llantas traseras presentan desgaste irregular, considerar alineación.",
            },
        ]
        for s in SERVICIOS:
            result = await db.execute(
                select(ServicioRealizado).where(ServicioRealizado.asignacion_id == s["asignacion"].id)
            )
            srv = result.scalar_one_or_none()
            if srv:
                print(f"  [skip] ServicioRealizado para asignación #{s['asignacion'].id}")
            else:
                srv = ServicioRealizado(
                    asignacion_id=s["asignacion"].id,
                    descripcion_trabajo=s["descripcion"],
                    repuestos=s["repuestos"],
                    observaciones=s["observaciones"],
                )
                db.add(srv)
                await db.flush()
                print(f"  [ok]   ServicioRealizado #{srv.id} para asignación #{s['asignacion'].id}")
        await db.commit()

        # ── 8. Cotizaciones ───────────────────────────────────────────────────
        print("\n[8/8] Cotizaciones...")
        COTIZACIONES = [
            {
                "incidente": incidentes[2],
                "items": [
                    {"descripcion": "Junta del carter",        "cantidad": 1, "precio_unitario": 85.0},
                    {"descripcion": "Aceite de motor 5W-30",   "cantidad": 4, "precio_unitario": 45.0},
                    {"descripcion": "Mano de obra reparación", "cantidad": 1, "precio_unitario": 150.0},
                ],
                "estado": "aceptada",
            },
            {
                "incidente": incidentes[3],
                "items": [
                    {"descripcion": "Pastillas de freno delanteras", "cantidad": 1, "precio_unitario": 120.0},
                    {"descripcion": "Disco de freno",                "cantidad": 2, "precio_unitario": 200.0},
                    {"descripcion": "Líquido de frenos DOT4",        "cantidad": 1, "precio_unitario": 35.0},
                    {"descripcion": "Mano de obra",                  "cantidad": 1, "precio_unitario": 180.0},
                ],
                "estado": "pendiente",
            },
            {
                "incidente": incidentes[7],
                "items": [
                    {"descripcion": "Aceite sintético 5W-40", "cantidad": 4, "precio_unitario": 55.0},
                    {"descripcion": "Filtro de aceite",       "cantidad": 1, "precio_unitario": 30.0},
                    {"descripcion": "Filtro de aire",         "cantidad": 1, "precio_unitario": 40.0},
                    {"descripcion": "Revisión general",       "cantidad": 1, "precio_unitario": 100.0},
                ],
                "estado": "pendiente",
            },
        ]
        for c in COTIZACIONES:
            result = await db.execute(
                select(Cotizacion).where(
                    Cotizacion.incidente_id == c["incidente"].id,
                    Cotizacion.taller_id == taller_principal.id,
                )
            )
            cot = result.scalar_one_or_none()
            if cot:
                print(f"  [skip] Cotización para incidente #{c['incidente'].id}")
            else:
                monto = sum(i["cantidad"] * i["precio_unitario"] for i in c["items"])
                cot = Cotizacion(
                    incidente_id=c["incidente"].id,
                    taller_id=taller_principal.id,
                    monto_estimado=monto,
                    detalle=json.dumps(c["items"]),
                    estado=c["estado"],
                )
                db.add(cot)
                await db.flush()
                print(f"  [ok]   Cotización #{cot.id} Bs.{monto:.2f} ({c['estado']}) para incidente #{c['incidente'].id}")
        await db.commit()

    print("""
╔══════════════════════════════════════════════════════════════════════╗
║                        SEED COMPLETADO                              ║
╠══════════════════════════════════════════════════════════════════════╣
║  CREDENCIALES (password: 12345678 para todos)                       ║
║  admin@rutasegura.com       → admin (sin tenant)                    ║
║  carlos@gmail.com           → cliente  (tenant: default)            ║
║  ana@gmail.com              → cliente  (tenant: default)            ║
║  autofix@gmail.com          → taller   (tenant: autofix_scz)        ║
║  mecanica_alemana@gmail.com → taller   (tenant: default)            ║
║  equipetrol_autos@gmail.com → taller   (tenant: default)            ║
║  motores_norte@gmail.com    → taller   (tenant: default)            ║
║  luis@gmail.com             → tecnico  (tenant: autofix_scz)        ║
║  pedro@gmail.com            → tecnico  (tenant: autofix_scz)        ║
╠══════════════════════════════════════════════════════════════════════╣
║  TENANTS                                                            ║
║  default     → Plataforma RutaSegura (activo)                       ║
║  autofix_scz → AutoFix Red SCZ (activo)                             ║
║  demo        → Demo Corp (inactivo)                                 ║
╠══════════════════════════════════════════════════════════════════════╣
║  TALLERES — Santa Cruz de la Sierra                                 ║
║  AutoFix Express SCZ   Av. Alemana 2do Anillo    aprobado  ★4.7    ║
║  Mecánica Alemana      Av. Cañoto esq. Beni      aprobado  ★4.3    ║
║  Equipetrol Autos      Av. San Martín Equipetrol  aprobado  ★3.9   ║
║  Motores del Norte     Plan 3000, 4to Anillo      pendiente ★0.0   ║
╠══════════════════════════════════════════════════════════════════════╣
║  4 Vehículos  │  5 Técnicos  │  8 Incidentes  │  8 Asignaciones    ║
║  2 ServiciosRealizados  │  3 Cotizaciones (1 aceptada, 2 pend.)     ║
╚══════════════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except Exception as exc:
        print(f"[seed] Error al ejecutar seed (continuando): {exc}")
