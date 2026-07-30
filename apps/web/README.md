# Percepta — Dashboard (Angular 15)

Frontend del dashboard. **Pendiente de generación** con Angular CLI 15 (requiere `npm i -g @angular/cli@15`):

```bash
cd apps
ng new web --routing --style=scss --skip-git
cd web
ng add @angular/material
```

## Arquitectura prevista (ver docs/07)

- **Feature modules con lazy loading**: `dashboard`, `cameras`, `events` (cola de revisión), `analytics`, `admin`.
- **Core**: interceptores (auth/refresh JWT), guards RBAC (permisos de `@percepta/contracts`), servicio WS/SSE de tiempo real.
- **Formularios dinámicos**: renderizado del formulario de configuración de cada módulo a partir de su `config.schema.json` (JSON Schema → Angular Reactive Forms).
- **Vista en vivo**: WebRTC vía endpoint canónico WHEP (`POST /api/v1/cameras/{id}/live/whep`).
- **i18n**: los enums llegan en inglés canónico (`new/acknowledged/...`) y se localizan con las keys de `EVENT_STATUS_I18N`.

El contrato de tipos se importa de `@percepta/contracts` (mismo workspace).
