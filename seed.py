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
    # Admin
    {"email": "admin@rutasegura.com",        "username": "admin",      "full_name": "Administrador",           "password": "12345678", "role": "admin"},
    # Clientes
    {"email": "carlos@gmail.com",            "username": "carlos",     "full_name": "Carlos Mendoza",          "password": "12345678", "role": "cliente"},
    {"email": "ana@gmail.com",               "username": "ana",        "full_name": "Ana Quispe",              "password": "12345678", "role": "cliente"},
    # Talleres (5)
    {"email": "autofix@gmail.com",           "username": "autofix",    "full_name": "AutoFix Express SCZ",     "password": "12345678", "role": "taller"},
    {"email": "mecanica_alemana@gmail.com",  "username": "malemana",   "full_name": "Mecánica Alemana",        "password": "12345678", "role": "taller"},
    {"email": "equipetrol_autos@gmail.com",  "username": "equipeautos","full_name": "Equipetrol Autos",        "password": "12345678", "role": "taller"},
    {"email": "motores_norte@gmail.com",     "username": "mtnorte",    "full_name": "Motores del Norte",       "password": "12345678", "role": "taller"},
    {"email": "techcar@gmail.com",           "username": "techcar",    "full_name": "TechCar Solutions",       "password": "12345678", "role": "taller"},
    # Técnicos (6 con cuenta — para 3 talleres distintos)
    {"email": "luis@gmail.com",              "username": "luis",       "full_name": "Luis Vargas",             "password": "12345678", "role": "tecnico"},
    {"email": "pedro@gmail.com",             "username": "pedro",      "full_name": "Pedro Huanca",            "password": "12345678", "role": "tecnico"},
    {"email": "marco@gmail.com",             "username": "marco",      "full_name": "Marco Flores",            "password": "12345678", "role": "tecnico"},
    {"email": "ivan@gmail.com",              "username": "ivan",       "full_name": "Iván Rojas",              "password": "12345678", "role": "tecnico"},
    {"email": "sofia@gmail.com",             "username": "sofia",      "full_name": "Sofía Peredo",            "password": "12345678", "role": "tecnico"},
    {"email": "jhon@gmail.com",              "username": "jhon",       "full_name": "Jhon Mamani",             "password": "12345678", "role": "tecnico"},
]


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:

        # ── 0. Tenants ────────────────────────────────────────────────────────
        print("\n[0/8] Tenants...")
        TENANTS = [
            {"nombre": "Plataforma RutaSegura", "slug": "default",     "descripcion": "Tenant principal de la plataforma",            "activo": True},
            {"nombre": "AutoFix Red SCZ",        "slug": "autofix_scz", "descripcion": "Red de talleres AutoFix en Santa Cruz",         "activo": True},
            {"nombre": "Demo Corp",              "slug": "demo",        "descripcion": "Tenant de demostración",                        "activo": False},
        ]
        tenants_map: dict[str, Tenant] = {}
        for data in TENANTS:
            result = await db.execute(select(Tenant).where(Tenant.slug == data["slug"]))
            ten = result.scalar_one_or_none()
            if ten:
                print(f"  [skip] {data['slug']}")
            else:
                ten = Tenant(nombre=data["nombre"], slug=data["slug"],
                             descripcion=data["descripcion"], activo=data["activo"])
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
            "admin":       None,
            "carlos":      default_tenant,
            "ana":         default_tenant,
            # talleres
            "autofix":     autofix_tenant,
            "malemana":    default_tenant,
            "equipeautos": default_tenant,
            "mtnorte":     default_tenant,
            "techcar":     default_tenant,
            # técnicos
            "luis":        autofix_tenant,   # AutoFix
            "pedro":       autofix_tenant,   # AutoFix
            "marco":       default_tenant,   # Mecánica Alemana
            "ivan":        default_tenant,   # Equipetrol Autos
            "sofia":       default_tenant,   # TechCar Solutions
            "jhon":        default_tenant,   # TechCar Solutions
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
                print(f"  [ok]   {data['email']} ({data['role']}) → {ten.slug if ten else 'sin tenant'}")
            users[data["username"]] = u
        await db.commit()
        for key in users:
            await db.refresh(users[key])

        # ── 2. Vehículos ──────────────────────────────────────────────────────
        print("\n[2/8] Vehículos...")
        VEHICULOS = [
            {"usuario": "carlos", "placa": "1234SCZ", "marca": "Toyota",  "modelo": "Hilux",    "anio": 2020, "color": "Blanco"},
            {"usuario": "carlos", "placa": "5678SCZ", "marca": "Honda",   "modelo": "HR-V",     "anio": 2022, "color": "Negro"},
            {"usuario": "ana",    "placa": "9012SCZ", "marca": "Hyundai", "modelo": "Tucson",   "anio": 2019, "color": "Plata"},
            {"usuario": "ana",    "placa": "3456SCZ", "marca": "Kia",     "modelo": "Sportage", "anio": 2021, "color": "Rojo"},
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
                    placa=v["placa"], marca=v["marca"],
                    modelo=v["modelo"], anio=v["anio"], color=v["color"],
                )
                db.add(veh)
                await db.flush()
                print(f"  [ok]   {v['placa']} ({v['marca']} {v['modelo']})")
            vehiculos[v["placa"]] = veh
        await db.commit()
        for k in vehiculos:
            await db.refresh(vehiculos[k])

        # ── 3. Talleres (5, todos Santa Cruz de la Sierra) ────────────────────
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
                "especialidades": ["motor_transmision", "diagnostico_obd", "electrica"],
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
                "especialidades": ["motor_transmision", "frenos_suspension", "electrica"],
                "tenant": default_tenant,
            },
            {
                "usuario": "equipeautos",
                "nombre": "Equipetrol Autos",
                "direccion": "Av. San Martín 3er Anillo Nro. 875, Barrio Equipetrol, Santa Cruz",
                "telefono": "76123456",
                "email_comercial": "equipetrol_autos@gmail.com",
                "latitud": -17.7681, "longitud": -63.2147,
                "estado": "aprobado", "disponible": True, "rating": 3.9,
                "especialidades": ["carroceria_pintura", "chaperia", "llanteria"],
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
                "especialidades": ["motor_transmision", "frenos_suspension"],
                "tenant": default_tenant,
            },
            {
                "usuario": "techcar",
                "nombre": "TechCar Solutions",
                "direccion": "Av. Busch 1er Anillo Nro. 318, Zona Norte, Santa Cruz de la Sierra",
                "telefono": "75987654",
                "email_comercial": "techcar@gmail.com",
                "latitud": -17.7560, "longitud": -63.2205,
                "estado": "aprobado", "disponible": True, "rating": 4.1,
                "especialidades": ["diagnostico_obd", "electrica", "motor_transmision"],
                "tenant": default_tenant,
            },
        ]
        talleres: dict[str, Taller] = {}
        for t in TALLERES:
            result = await db.execute(select(Taller).where(Taller.usuario_id == users[t["usuario"]].id))
            tal = result.scalar_one_or_none()
            if tal:
                # actualizar especialidades si están vacías
                if not tal.especialidades:
                    tal.especialidades = json.dumps(t["especialidades"])
                    await db.flush()
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
                    especialidades=json.dumps(t["especialidades"]),
                    tenant_id=t["tenant"].id,
                )
                db.add(tal)
                await db.flush()
                print(f"  [ok]   {t['nombre']} ({t['estado']}) ★{t['rating']} → {t['latitud']}, {t['longitud']}")
            talleres[t["usuario"]] = tal
        await db.commit()
        for k in talleres:
            await db.refresh(talleres[k])

        # ── 4. Técnicos (con usuarios vinculados para 3 talleres) ─────────────
        print("\n[4/8] Técnicos...")

        # Técnicos por taller: clave = usuario del taller
        TECNICOS_POR_TALLER = {
            # ── AutoFix Express SCZ (autofix_scz) ──────────────────────────────
            "autofix": [
                {"nombre": "Luis Vargas",   "especialidad": "Motor y transmisión",   "telefono": "71111111", "estado": "disponible", "usuario": "luis"},
                {"nombre": "Pedro Huanca",  "especialidad": "Eléctrica automotriz",  "telefono": "72222222", "estado": "disponible", "usuario": "pedro"},
                {"nombre": "Jorge Mamani",  "especialidad": "Frenos y suspensión",   "telefono": "73333333", "estado": "disponible", "usuario": None},
                {"nombre": "Rosa Chávez",   "especialidad": "Carrocería y pintura",  "telefono": "74444444", "estado": "disponible", "usuario": None},
            ],
            # ── Mecánica Alemana (default) ─────────────────────────────────────
            "malemana": [
                {"nombre": "Marco Flores",  "especialidad": "Motor y diagnóstico",   "telefono": "76111111", "estado": "disponible", "usuario": "marco"},
                {"nombre": "Diego Peña",    "especialidad": "Frenos y suspensión",   "telefono": "76222222", "estado": "disponible", "usuario": None},
                {"nombre": "Carmen Roca",   "especialidad": "Eléctrica automotriz",  "telefono": "76333333", "estado": "disponible", "usuario": None},
            ],
            # ── Equipetrol Autos (default) ─────────────────────────────────────
            "equipeautos": [
                {"nombre": "Iván Rojas",    "especialidad": "Carrocería y pintura",  "telefono": "77111111", "estado": "disponible", "usuario": "ivan"},
                {"nombre": "Laura Suárez",  "especialidad": "Chapería y soldadura",  "telefono": "77222222", "estado": "disponible", "usuario": None},
            ],
            # ── TechCar Solutions (default) ────────────────────────────────────
            "techcar": [
                {"nombre": "Sofía Peredo",  "especialidad": "Diagnóstico OBD",       "telefono": "78111111", "estado": "disponible", "usuario": "sofia"},
                {"nombre": "Jhon Mamani",   "especialidad": "Eléctrica y ADAS",      "telefono": "78222222", "estado": "disponible", "usuario": "jhon"},
                {"nombre": "Raúl Méndez",   "especialidad": "Motor y transmisión",   "telefono": "78333333", "estado": "disponible", "usuario": None},
            ],
            # ── Motores del Norte: pendiente, sin técnicos aún ─────────────────
        }

        tecnicos_por_taller: dict[str, list[Tecnico]] = {}
        for taller_key, lista in TECNICOS_POR_TALLER.items():
            taller_obj = talleres[taller_key]
            teclist: list[Tecnico] = []
            for t in lista:
                result = await db.execute(
                    select(Tecnico).where(
                        Tecnico.taller_id == taller_obj.id,
                        Tecnico.nombre == t["nombre"],
                    )
                )
                tec = result.scalar_one_or_none()
                if tec:
                    print(f"  [skip] {t['nombre']} ({taller_key})")
                else:
                    uid = users[t["usuario"]].id if t["usuario"] else None
                    tec = Tecnico(
                        taller_id=taller_obj.id,
                        usuario_id=uid,
                        nombre=t["nombre"],
                        especialidad=t["especialidad"],
                        telefono=t["telefono"],
                        estado=t["estado"],
                        activo=True,
                    )
                    db.add(tec)
                    await db.flush()
                    vinculo = f"→ {t['usuario']}@gmail.com" if t["usuario"] else "sin cuenta"
                    print(f"  [ok]   {t['nombre']} ({taller_key}) {vinculo}")
                teclist.append(tec)
            tecnicos_por_taller[taller_key] = teclist
        await db.commit()
        for lst in tecnicos_por_taller.values():
            for tec in lst:
                await db.refresh(tec)

        # ── Atajos ────────────────────────────────────────────────────────────
        taller_principal  = talleres["autofix"]
        tec_luis          = tecnicos_por_taller["autofix"][0]
        tec_pedro         = tecnicos_por_taller["autofix"][1]
        tec_marco         = tecnicos_por_taller["malemana"][0]
        tec_ivan          = tecnicos_por_taller["equipeautos"][0]

        # ── 5. Incidentes (ubicados en Santa Cruz) ────────────────────────────
        print("\n[5/8] Incidentes...")
        INCIDENTES = [
            # resueltos → historial
            {"usuario": "carlos", "placa": "1234SCZ", "lat": -17.7730, "lon": -63.2090, "descripcion": "Vehículo no enciende, batería descargada",                    "estado": "resuelto",   "prioridad": "alta"},
            {"usuario": "ana",    "placa": "9012SCZ", "lat": -17.7850, "lon": -63.1820, "descripcion": "Pinchazo de llanta delantera derecha",                        "estado": "resuelto",   "prioridad": "media"},
            # en_proceso → asignaciones activas
            {"usuario": "carlos", "placa": "5678SCZ", "lat": -17.7900, "lon": -63.1750, "descripcion": "Fuga de aceite por el carter, humo blanco",                   "estado": "en_proceso", "prioridad": "alta"},
            {"usuario": "ana",    "placa": "3456SCZ", "lat": -17.7680, "lon": -63.2150, "descripcion": "Frenos no responden correctamente al frenar",                  "estado": "en_proceso", "prioridad": "alta"},
            {"usuario": "carlos", "placa": "1234SCZ", "lat": -17.7760, "lon": -63.2010, "descripcion": "Recalentamiento del motor, temperatura muy alta",              "estado": "en_proceso", "prioridad": "alta"},
            {"usuario": "ana",    "placa": "9012SCZ", "lat": -17.8100, "lon": -63.1600, "descripcion": "Ruido extraño al acelerar, posible problema en transmisión",   "estado": "en_proceso", "prioridad": "media"},
            # pendiente → sin asignación
            {"usuario": "carlos", "placa": "5678SCZ", "lat": -17.7620, "lon": -63.1980, "descripcion": "Luces del tablero parpadeando, posible falla eléctrica",       "estado": "pendiente",  "prioridad": "baja"},
            # en_proceso → para cotización
            {"usuario": "ana",    "placa": "3456SCZ", "lat": -17.7950, "lon": -63.1700, "descripcion": "Cambio de aceite y revisión general preventiva",               "estado": "en_proceso", "prioridad": "baja"},
        ]
        incidentes: list[Incidente] = []
        for inc_data in INCIDENTES:
            result = await db.execute(
                select(Incidente).where(
                    Incidente.usuario_id == users[inc_data["usuario"]].id,
                    Incidente.descripcion == inc_data["descripcion"],
                )
            )
            inc = result.scalar_one_or_none()
            if inc:
                print(f"  [skip] Incidente ya existe — {inc_data['descripcion'][:55]}...")
            else:
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
                print(f"  [ok]   Incidente #{inc.id} ({inc_data['prioridad']}) — {inc_data['descripcion'][:55]}...")
            incidentes.append(inc)
        await db.commit()
        for inc in incidentes:
            await db.refresh(inc)

        # ── 6. Asignaciones (distribuidas en varios talleres) ─────────────────
        print("\n[6/8] Asignaciones...")
        ASIGNACIONES = [
            # AutoFix — historial finalizado
            {"incidente": incidentes[0], "taller": taller_principal,      "tecnico": tec_luis,  "estado": "finalizado",    "eta": None, "obs": "Servicio completado exitosamente"},
            {"incidente": incidentes[1], "taller": taller_principal,      "tecnico": tec_pedro, "estado": "finalizado",    "eta": None, "obs": "Llanta cambiada sin inconvenientes"},
            # AutoFix — activos
            {"incidente": incidentes[2], "taller": taller_principal,      "tecnico": tec_luis,  "estado": "en_reparacion", "eta": 30,   "obs": "Diagnóstico completado, en proceso de reparación"},
            {"incidente": incidentes[4], "taller": taller_principal,      "tecnico": tec_pedro, "estado": "en_camino",     "eta": 15,   "obs": "Técnico en camino al lugar"},
            # Mecánica Alemana — activos
            {"incidente": incidentes[3], "taller": talleres["malemana"],  "tecnico": tec_marco, "estado": "en_reparacion", "eta": 45,   "obs": "Revisando sistema de frenos"},
            {"incidente": incidentes[5], "taller": talleres["malemana"],  "tecnico": None,      "estado": "aceptado",      "eta": None, "obs": None},
            # Equipetrol — activo
            {"incidente": incidentes[6], "taller": talleres["equipeautos"],"tecnico": tec_ivan, "estado": "en_sitio",      "eta": 0,    "obs": "Técnico evaluando en el lugar"},
            # AutoFix — cotización pendiente
            {"incidente": incidentes[7], "taller": taller_principal,      "tecnico": None,      "estado": "aceptado",      "eta": None, "obs": None},
        ]
        asignaciones: list[Asignacion] = []
        for a in ASIGNACIONES:
            result = await db.execute(
                select(Asignacion).where(
                    Asignacion.incidente_id == a["incidente"].id,
                    Asignacion.taller_id == a["taller"].id,
                )
            )
            asig = result.scalar_one_or_none()
            tec_nombre = a["tecnico"].nombre if a["tecnico"] else "Sin técnico"
            taller_nombre = a["taller"].nombre
            if asig:
                print(f"  [skip] Asignación #{asig.id} ya existe — {taller_nombre} · {tec_nombre}")
            else:
                asig = Asignacion(
                    incidente_id=a["incidente"].id,
                    taller_id=a["taller"].id,
                    tecnico_id=a["tecnico"].id if a["tecnico"] else None,
                    estado=a["estado"],
                    eta=a["eta"],
                    observacion=a["obs"],
                )
                db.add(asig)
                await db.flush()
                print(f"  [ok]   Asignación #{asig.id} ({a['estado']}) · {taller_nombre} · {tec_nombre}")
            asignaciones.append(asig)
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
                    {"descripcion": "Batería 12V 60Ah",      "cantidad": 1},
                    {"descripcion": "Terminales de batería", "cantidad": 2},
                ]),
                "observaciones": "Se recomienda revisión eléctrica completa en 6 meses.",
            },
            {
                "asignacion": asignaciones[1],
                "descripcion": "Cambio de llanta delantera derecha por pinchazo. Se revisaron todas las llantas y se ajustó presión.",
                "repuestos": json.dumps([
                    {"descripcion": "Llanta 195/65 R15", "cantidad": 1},
                    {"descripcion": "Parche vulcanizado",  "cantidad": 1},
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
                "taller": taller_principal,
                "items": [
                    {"descripcion": "Junta del carter",        "cantidad": 1, "precio_unitario": 85.0},
                    {"descripcion": "Aceite de motor 5W-30",   "cantidad": 4, "precio_unitario": 45.0},
                    {"descripcion": "Mano de obra reparación", "cantidad": 1, "precio_unitario": 150.0},
                ],
                "estado": "aceptada",
            },
            {
                "incidente": incidentes[3],
                "taller": talleres["malemana"],
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
                "taller": taller_principal,
                "items": [
                    {"descripcion": "Aceite sintético 5W-40", "cantidad": 4, "precio_unitario": 55.0},
                    {"descripcion": "Filtro de aceite",        "cantidad": 1, "precio_unitario": 30.0},
                    {"descripcion": "Filtro de aire",          "cantidad": 1, "precio_unitario": 40.0},
                    {"descripcion": "Revisión general",        "cantidad": 1, "precio_unitario": 100.0},
                ],
                "estado": "pendiente",
            },
        ]
        for c in COTIZACIONES:
            result = await db.execute(
                select(Cotizacion).where(
                    Cotizacion.incidente_id == c["incidente"].id,
                    Cotizacion.taller_id == c["taller"].id,
                )
            )
            cot = result.scalar_one_or_none()
            if cot:
                print(f"  [skip] Cotización para incidente #{c['incidente'].id}")
            else:
                monto = sum(i["cantidad"] * i["precio_unitario"] for i in c["items"])
                cot = Cotizacion(
                    incidente_id=c["incidente"].id,
                    taller_id=c["taller"].id,
                    monto_estimado=monto,
                    detalle=json.dumps(c["items"]),
                    estado=c["estado"],
                )
                db.add(cot)
                await db.flush()
                print(f"  [ok]   Cotización #{cot.id} Bs.{monto:.2f} ({c['estado']}) · {c['taller'].nombre}")
        await db.commit()

    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                          SEED COMPLETADO                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  CREDENCIALES  (password: 12345678 para todos)                              ║
╠══════════════════════════╦══════════════╦════════════════════════════════════╣
║  EMAIL                   ║  ROL         ║  TENANT                            ║
╠══════════════════════════╬══════════════╬════════════════════════════════════╣
║  admin@rutasegura.com    ║  admin       ║  —                                ║
║  carlos@gmail.com        ║  cliente     ║  default                          ║
║  ana@gmail.com           ║  cliente     ║  default                          ║
╠══════════════════════════╬══════════════╬════════════════════════════════════╣
║  autofix@gmail.com       ║  taller      ║  autofix_scz  ★4.7  aprobado      ║
║  mecanica_alemana@       ║  taller      ║  default      ★4.3  aprobado      ║
║  equipetrol_autos@       ║  taller      ║  default      ★3.9  aprobado      ║
║  techcar@gmail.com       ║  taller      ║  default      ★4.1  aprobado      ║
║  motores_norte@          ║  taller      ║  default      ★0.0  pendiente     ║
╠══════════════════════════╬══════════════╬════════════════════════════════════╣
║  luis@gmail.com          ║  tecnico     ║  autofix_scz  (AutoFix)           ║
║  pedro@gmail.com         ║  tecnico     ║  autofix_scz  (AutoFix)           ║
║  marco@gmail.com         ║  tecnico     ║  default      (Mec. Alemana)      ║
║  ivan@gmail.com          ║  tecnico     ║  default      (Equipetrol)        ║
║  sofia@gmail.com         ║  tecnico     ║  default      (TechCar)           ║
║  jhon@gmail.com          ║  tecnico     ║  default      (TechCar)           ║
╠══════════════════════════╩══════════════╩════════════════════════════════════╣
║  TALLERES — Santa Cruz de la Sierra                                         ║
║  AutoFix Express SCZ    Av. Alemana 2do Anillo       autofix_scz  ★4.7     ║
║  Mecánica Alemana       Av. Cañoto esq. Beni         default      ★4.3     ║
║  Equipetrol Autos       Av. San Martín Equipetrol    default      ★3.9     ║
║  TechCar Solutions      Av. Busch 1er Anillo         default      ★4.1     ║
║  Motores del Norte      Plan 3000, 4to Anillo        default      ★0.0 *   ║
║  * pendiente de aprobación                                                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  ESPECIALIDADES                                                             ║
║  AutoFix      → motor_transmision, diagnostico_obd, electrica              ║
║  Mec. Alemana → motor_transmision, frenos_suspension, electrica            ║
║  Equipetrol   → carroceria_pintura, chaperia, llanteria                    ║
║  TechCar      → diagnostico_obd, electrica, motor_transmision              ║
║  Motores N.   → motor_transmision, frenos_suspension                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  4 Vehículos │ 12 Técnicos (6 con cuenta) │ 8 Incidentes │ 8 Asignaciones  ║
║  2 ServiciosRealizados │ 3 Cotizaciones (1 aceptada, 2 pendientes)         ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except Exception as exc:
        print(f"[seed] Error al ejecutar seed (continuando): {exc}")
