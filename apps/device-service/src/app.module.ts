import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { join } from 'node:path';
import { HealthController } from './health.controller';
import { DatabaseService } from './db/database.service';
import { CamerasController } from './cameras/cameras.controller';
import { CamerasRepository } from './cameras/cameras.repository';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      // El .env vive en la raíz del monorepo.
      envFilePath: [join(__dirname, '../../../.env'), '.env'],
    }),
  ],
  controllers: [HealthController, CamerasController],
  providers: [DatabaseService, CamerasRepository],
})
export class AppModule {}
