> Parte de la documentación de arquitectura de **Percepta** — Plataforma SaaS de Análisis Inteligente de Video con IA modular. Ver [índice](README.md).
> ⚠️ **Ante cualquier conflicto de contrato (nombres de columna, enums, firmas, esquemas), manda [CONTRACTS.md](CONTRACTS.md)** — este documento describe la arquitectura y el *porqué*; los detalles congelados para implementación viven allí.

# Especificación de UI (referencia visual aprobada)

Basado en los mockups aprobados por el usuario el 2026-07-31. Esta es la **dirección visual de referencia** para `apps/web`.

> **Nota de marca:** los mockups usan el nombre **VisionAI**. `Percepta` era el codename interno del diseño. Decisión de producto pendiente; el código usa `percepta-*` en paquetes/servicios (cambiarlo es un rename mecánico, no arquitectónico).

---

## 1. Sistema visual

| Elemento | Valor |
|---|---|
| Tema | **Oscuro por defecto** (`#0d1117`–`#111827` de fondo, paneles `#161b22`/`#1a2130`) |
| Acento primario | Azul (`#2563eb` / `#3b82f6`) — nav activo, botones primarios, series de gráficos |
| Bordes | 1px sutiles (`#232b3a`), radio 10–12px, sin sombras duras |
| Tipografía | Sans del sistema; títulos 20–24px semibold, métricas 28–32px bold, meta 12px |
| Severidad | `Crítico` rojo · `Alto` naranja · `Medio` azul/amarillo · `Bajo` gris |
| Estado | `En línea` punto verde · `LIVE` badge rojo sobre el video |

**Layout general:** sidebar fija a la izquierda (~220px) + área de contenido. Sidebar: logo arriba, nav (Dashboard, Cámaras, Eventos, Usuarios), y **usuario al pie** con avatar y rol.

**Header de página:** título + subtítulo a la izquierda; a la derecha filtros globales — *selector de empresa*, *selector de sucursal* y *rango de fechas*. Esos tres filtros son la materialización de la multitenancy y del scoping por sucursal.

---

## 2. Pantalla: Dashboard

Cuatro bandas verticales:

**a) Fila de KPIs (5 tarjetas)** — cada una con ícono en cuadro de color, etiqueta, valor grande y variación vs. ayer:
- Cámaras conectadas `48 / 52` + «92% en línea»
- Eventos hoy `124` ↑18%
- Eventos críticos `24` ↑33%
- Personas detectadas `1.429` ↑12%
- Almacenamiento `2.45 TB / 10 TB` + barra de progreso 24%

**b) Cámaras en vivo (columna ancha)** — grid 3×2 de tarjetas. Cada una: nombre `01 - Entrada Principal`, badge `En línea`, thumbnail del stream con timestamp overlay y badge `LIVE`, y una barra de acciones (grabar, zoom, configurar, indicador REC). Controles de vista: densidad de grid y selector de cámaras, botón de pantalla completa.

**c) Eventos recientes (columna derecha)** — lista con ícono por módulo, título del evento, `Sucursal - Cámara`, **badge de severidad**, hora, y **thumbnail de la evidencia**. Enlace «Ver todos».

**d) Analítica (3 paneles)**:
- Donut «Eventos por tipo (hoy)» con total al centro y leyenda con conteo y porcentaje
- Área/línea «Eventos por hora (hoy)» con tooltip
- «Top módulos IA» — lista con barra de progreso y porcentaje

---

## 3. Pantalla: Cámaras — **el corazón del producto**

Interacción central: **arrastrar y soltar un módulo de IA sobre una cámara**.

**a) Fila de KPIs:** Cámaras totales `48` (conectadas 42) · Módulos activos `156` (en uso 93%) · Eventos hoy · Almacenamiento · Ancho de banda `215 Mbps`.

**b) Columna izquierda — «Cámaras disponibles»** («Arrastra módulos a cada cámara»):
- Toggle **Vista de cuadrícula / Vista de lista**
- Tarjeta por cámara: thumbnail, nombre, `En línea`, contador **«N módulos activos»** y una **zona de drop** punteada con el texto «Arrastra módulos aquí» + ícono de info
- Al arrastrar sobre una tarjeta, su zona de drop se resalta (borde azul) y se dibuja una guía punteada desde el módulo origen
- Paginación (`Mostrando 6 de 48 cámaras`)

**c) Columna derecha — «Módulos de IA disponibles»** («Arrastra y suelta sobre una cámara»):
- **Chips de filtro por categoría**: Todos · Seguridad · Personas · Operaciones · Logística
- Lista de módulos arrastrables: handle de agarre, ícono de color, nombre, descripción de una línea, y **badge de categoría**
- Módulos mostrados: Posible robo/actividad sospechosa, Registro de caídas, Uso de EPP, Conteo de personas, Zona restringida, Objetos abandonados, Humo/fuego, Detección de vehículos, Conteo de mercancías/pallets, Permanencia excesiva
- Mientras se arrastra: **preview flotante** del módulo siguiendo al cursor

**d) Pie:** «Configuración global» con botón **Configurar reglas**.

> Al soltar un módulo sobre una cámara se crea una fila en `camera_module_configs`, y debe abrirse el **formulario de configuración generado dinámicamente desde el `config.schema.json`** de ese módulo (CONTRACTS §4). Esa es la unión entre esta UI y el sistema de plugins.

---

## 4. Consecuencias para el diseño ya definido

1. **La UI valida el modelo de datos**: «N módulos activos» por cámara es exactamente `camera_module_configs`, y los badges de categoría son `ai_modules.category`.
2. **Los filtros de empresa/sucursal del header** requieren enforcar `siteIds` en la API (hoy el token lo transporta pero el `event-service` no lo aplica — hallazgo abierto de la revisión).
3. **Las miniaturas de evidencia** en «Eventos recientes» consumen `evidences` vía enlaces firmados de MinIO.
4. **Human-in-the-loop**: la lista de eventos necesita las acciones de revisión (reconocer/resolver) y mostrar el estado; el mockup aún no las muestra — hay que sumarlas, porque son el núcleo ético del sistema.

---

[Índice](README.md) · [⬅ Operación y MLOps](09-operacion-observabilidad-y-mlops.md)
