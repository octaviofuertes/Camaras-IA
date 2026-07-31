import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { join } from 'node:path';
import { HealthController } from './health.controller';
import { DatabaseService } from './db/database.service';
import { EventsController } from './events/events.controller';
import { EventsService } from './events/events.service';
import { EventsRepository } from './events/events.repository';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      // El .env vive en la raíz del monorepo: con cwd = apps/<svc> no se
      // encontraría y las variables quedarían silenciosamente vacías.
      envFilePath: [join(__dirname, '../../../.env'), '.env'],
    }),
  ],
  controllers: [HealthController, EventsController],
  providers: [DatabaseService, EventsService, EventsRepository],
})
export class AppModule {}
