import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  app.setGlobalPrefix('api/v1');
  // media-service y ai-worker consumen esta API desde otro origen.
  app.enableCors({ origin: true });
  const port = Number(process.env.DEVICE_SERVICE_PORT ?? process.env.PORT ?? 3003);
  await app.listen(port);
  // eslint-disable-next-line no-console
  console.log(`[device-service] escuchando en :${port}`);
}
void bootstrap();
