#!/usr/bin/env node
/**
 * Emite un access token de DESARROLLO para probar los servicios sin tener
 * identity-service todavía. Usa el mismo JWT_ACCESS_SECRET y la misma forma de
 * claims que emitirá identity-service (CONTRACTS §9).
 *
 * Uso:  node tools/dev-token.js [rol] [organizationId]
 *       node tools/dev-token.js operator
 *       node tools/dev-token.js org_admin 00000000-0000-4000-b000-000000000001
 */
const fs = require('node:fs');
const path = require('node:path');
const jwt = require('jsonwebtoken');
const { SYSTEM_ROLE_PERMISSIONS, PERMISSIONS } = require('@percepta/contracts');

// Cargar .env de la raíz sin dependencias extra.
const envPath = path.join(__dirname, '..', '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
  }
}

const role = process.argv[2] || 'operator';
const org = process.argv[3] || '00000000-0000-4000-b000-000000000001';
const userId = process.argv[4] || '00000000-0000-4000-e000-000000000001';

const secret = process.env.JWT_ACCESS_SECRET;
if (!secret) {
  console.error('Falta JWT_ACCESS_SECRET (definila en .env)');
  process.exit(1);
}

// `service` no es un rol de usuario: es la identidad que usan los servicios del
// pipeline (ai-worker / rules-engine) para dar de alta eventos. Ningún humano
// tiene events:ingest.
let perms;
let ttl = process.env.JWT_ACCESS_TTL ? Number(process.env.JWT_ACCESS_TTL) : 900;
if (role === 'service') {
  // `persons:read` le deja al módulo traer la galería de rostros de los
  // empleados dados de alta. NO incluye `persons:write`: un servicio no da
  // de alta a nadie, eso lo hace una persona registrando el consentimiento.
  perms = [
    'events:ingest', 'events:read', 'cameras:read',
    'camera-module-configs:read', 'modules:read', 'persons:read',
  ];
  // Un servicio no puede volver a autenticarse solo: el ai-worker lee el token
  // del entorno una vez y no lo renueva. Con el TTL de 15 minutos de un humano,
  // a los 15 minutos de arrancar TODA alta de evento pasaba a fallar con 401 y
  // el sistema dejaba de registrar caídas sin que nada lo dijera. En producción
  // esto lo resuelve client-credentials con refresh; en desarrollo, un token que
  // dura la sesión de trabajo.
  ttl = 12 * 60 * 60;
} else {
  const granted = SYSTEM_ROLE_PERMISSIONS[role];
  if (!granted) {
    console.error(
      `Rol desconocido: ${role}. Válidos: service, ${Object.keys(SYSTEM_ROLE_PERMISSIONS).join(', ')}`,
    );
    process.exit(1);
  }
  perms = granted[0] === '*' ? [...PERMISSIONS] : granted;
}

const token = jwt.sign({ sub: userId, org, perms }, secret, {
  algorithm: 'HS256',
  expiresIn: ttl,
});

process.stdout.write(token);
