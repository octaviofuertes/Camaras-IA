import { BadRequestException, Body, Controller, Post } from '@nestjs/common';
import { AuthService, type LoginResult } from './auth.service';

@Controller('auth')
export class AuthController {
  constructor(private readonly auth: AuthService) {}

  @Post('login')
  async login(@Body() body: Record<string, unknown>): Promise<LoginResult> {
    const email = String(body?.['email'] ?? '').trim();
    const password = String(body?.['password'] ?? '');
    if (!email || !password) throw new BadRequestException('Email y contraseña son obligatorios');
    return this.auth.login(email, password);
  }

  /** Token de la pantalla de bienvenida: un solo permiso, sin credenciales. */
  @Post('kiosk')
  async kiosk(): Promise<LoginResult> {
    return this.auth.kiosco();
  }

  @Post('refresh')
  async refresh(@Body() body: Record<string, unknown>): Promise<LoginResult> {
    const token = String(body?.['refreshToken'] ?? '');
    if (!token) throw new BadRequestException('refreshToken es obligatorio');
    return this.auth.refresh(token);
  }
}
