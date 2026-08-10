import type { Permission } from '@percepta/contracts';

/** Claims que el access token transporta (emitidos por identity-service). */
export interface AccessTokenClaims {
  sub: string; // user_id
  org: string; // organization_id — la fuente del contexto de tenant
  perms: Permission[];
  sites?: string[]; // scoping opcional por sucursal
  iat?: number;
  exp?: number;
}

export interface AuthContext {
  userId: string;
  organizationId: string;
  permissions: Permission[];
  siteIds?: string[];
}

declare module 'express' {
  interface Request {
    auth?: AuthContext;
  }
}
