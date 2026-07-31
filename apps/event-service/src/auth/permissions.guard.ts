import { CanActivate, ExecutionContext, ForbiddenException, Injectable, SetMetadata } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import type { Request } from 'express';
import type { Permission } from '@percepta/contracts';

export const PERMISSIONS_KEY = 'percepta:permissions';

/** Declara los permisos requeridos por un endpoint (catálogo canónico, CONTRACTS §9). */
export const RequirePermissions = (...perms: Permission[]) => SetMetadata(PERMISSIONS_KEY, perms);

/** Capa 2 de la defensa en profundidad: RBAC por endpoint. La capa 3 es la RLS. */
@Injectable()
export class PermissionsGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const required = this.reflector.getAllAndOverride<Permission[]>(PERMISSIONS_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (!required?.length) return true;

    const req = context.switchToHttp().getRequest<Request>();
    const granted = req.auth?.permissions ?? [];
    const missing = required.filter((p) => !granted.includes(p));
    if (missing.length) {
      throw new ForbiddenException(`Permisos faltantes: ${missing.join(', ')}`);
    }
    return true;
  }
}
