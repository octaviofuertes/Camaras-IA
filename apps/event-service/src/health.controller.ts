import { Controller, Get } from '@nestjs/common';
import { CONTRACTS_VERSION } from '@percepta/contracts';
import { DatabaseService } from './db/database.service';

@Controller('health')
export class HealthController {
  constructor(private readonly db: DatabaseService) {}

  @Get()
  async health(): Promise<{
    ok: boolean;
    service: string;
    contractsVersion: string;
    db: 'up' | 'down';
  }> {
    // El health refleja las dependencias reales: si Postgres no responde, el
    // servicio no está sano aunque el proceso siga en pie.
    let db: 'up' | 'down' = 'down';
    try {
      db = (await this.db.ping()) ? 'up' : 'down';
    } catch {
      db = 'down';
    }
    return { ok: db === 'up', service: 'event-service', contractsVersion: CONTRACTS_VERSION, db };
  }
}
