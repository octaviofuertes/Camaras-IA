import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  app.setGlobalPrefix('api/v1');
  app.enableCors({ origin: true });
  const port = Number(process.env.IDENTITY_SERVICE_PORT ?? process.env.PORT ?? 3001);
  await app.listen(port);
  // eslint-disable-next-line no-console
  console.log(`[identity-service] escuchando en :${port}`);
}
void bootstrap();
