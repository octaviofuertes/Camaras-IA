import { CanActivate, ExecutionContext, Injectable, UnauthorizedException } from '@nestjs/common';
import type { Request } from 'express';
import jwt from 'jsonwebtoken';
import type { AccessTokenClaims, AuthContext } from './auth.types';

/**
 * Capa 1 de la defensa en profundidad (CONTRACTS §9): verifica el access token.
 *
 * El `organization_id` SIEMPRE sale del token verificado, nunca del body, query
 * o headers: es lo que después alimenta `app.current_org` y, con él, la RLS.
 */
@Injectable()
export class JwtGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const req = context.switchToHttp().getRequest<Request>();
    const header = req.headers.authorization;
    if (!header?.startsWith('Bearer ')) {
      throw new UnauthorizedException('Falta el header Authorization: Bearer <token>');
    }
    const secret = process.env.JWT_ACCESS_SECRET;
    if (!secret) {
      throw new Error('JWT_ACCESS_SECRET no está definida');
    }

    let claims: AccessTokenClaims;
    try {
      claims = jwt.verify(header.slice(7), secret, { algorithms: ['HS256'] }) as AccessTokenClaims;
    } catch {
      throw new UnauthorizedException('Token inválido o expirado');
    }

    if (!claims.sub || !claims.org) {
      throw new UnauthorizedException('Token sin sub/org');
    }

    const auth: AuthContext = {
      userId: claims.sub,
      organizationId: claims.org,
      permissions: Array.isArray(claims.perms) ? claims.perms : [],
      siteIds: claims.sites,
    };
    req.auth = auth;
    return true;
  }
}
