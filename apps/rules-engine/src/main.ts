import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  app.setGlobalPrefix('api/v1');
  // 3006 y no 3005: analytics-service ya vive ahí, y lo apuntan el proxy del
  // frontend, el ai-worker y el módulo de ingreso de personas. Los dos leían
  // `PORT` a secas, así que con `pnpm dev` arrancaban en paralelo y el que
  // perdía la carrera moría con EADDRINUSE —casi siempre analytics, que es del
  // que cuelga la mitad del producto.
  const port = Number(process.env.RULES_ENGINE_PORT ?? process.env.PORT ?? 3006);
  await app.listen(port);
  // eslint-disable-next-line no-console
  console.log(`[rules-engine] escuchando en :${port}`);
}
void bootstrap();
