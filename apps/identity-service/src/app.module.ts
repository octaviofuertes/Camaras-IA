import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { join } from 'node:path';
import { HealthController } from './health.controller';
import { DatabaseService } from './db/database.service';
import { AuthController } from './auth/auth.controller';
import { AuthService } from './auth/auth.service';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: [join(__dirname, '../../../.env'), '.env'],
    }),
  ],
  controllers: [HealthController, AuthController],
  providers: [DatabaseService, AuthService],
})
export class AppModule {}
