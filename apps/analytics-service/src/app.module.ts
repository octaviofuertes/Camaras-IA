import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { join } from 'node:path';
import { HealthController } from './health.controller';
import { DatabaseService } from './db/database.service';
import { PersonsController } from './persons/persons.controller';
import { PersonsService } from './persons/persons.service';
import { PersonsRepository } from './persons/persons.repository';
import { ModuloAsignadoGuard } from './auth/modulo.guard';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      // El .env vive en la raíz del monorepo: con cwd = apps/<svc> no se
      // encontraría y las variables quedarían silenciosamente vacías.
      envFilePath: [join(__dirname, '../../../.env'), '.env'],
    }),
  ],
  controllers: [HealthController, PersonsController],
  providers: [
    DatabaseService,
    PersonsService, PersonsRepository,
    // El guard consulta la base, así que Nest lo tiene que construir él (los
    // que se listan en @UseGuards por clase se instancian con inyección).
    ModuloAsignadoGuard,
  ],
})
export class AppModule {}
