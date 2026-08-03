import { Controller, Get } from '@nestjs/common';
import { CONTRACTS_VERSION } from '@percepta/contracts';
import { DatabaseService } from './db/database.service';

@Controller('health')
export class HealthController {
  constructor(private readonly db: DatabaseService) {}

  @Get()
  async health(): Promise<{ ok: boolean; service: string; contractsVersion: string; db: 'up' | 'down' }> {
    let db: 'up' | 'down' = 'down';
    try {
      db = (await this.db.ping()) ? 'up' : 'down';
    } catch {
      db = 'down';
    }
    return { ok: db === 'up', service: 'device-service', contractsVersion: CONTRACTS_VERSION, db };
  }
}
