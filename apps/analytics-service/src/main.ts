import { NestFactory } from '@nestjs/core';
import type { NestExpressApplication } from '@nestjs/platform-express';
import { AppModule } from './app.module';

/**
 * Tamaño máximo del cuerpo de una petición.
 *
 * El valor por defecto son 100 KB y una foto en base64 pesa varios cientos: el
 * alta manual fallaba con "request entity too large". La pantalla ya achica las
 * fotos antes de mandarlas, así que esto es el margen y no el mecanismo — con
 * 8 MB entra una foto de teléfono aunque no se hubiera podido escalar.
 */
const LIMITE_CUERPO = '8mb';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create<NestExpressApplication>(AppModule);
  app.useBodyParser('json', { limit: LIMITE_CUERPO });
  app.setGlobalPrefix('api/v1');
  const port = Number(process.env.PORT ?? 3005);
  await app.listen(port);
  // eslint-disable-next-line no-console
  console.log(`[analytics-service] escuchando en :${port}`);
}
void bootstrap();
