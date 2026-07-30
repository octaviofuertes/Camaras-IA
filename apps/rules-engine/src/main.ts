import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  app.setGlobalPrefix('api/v1');
  const port = Number(process.env.PORT ?? 3005);
  await app.listen(port);
  // eslint-disable-next-line no-console
  console.log(`[rules-engine] escuchando en :${port}`);
}
void bootstrap();
