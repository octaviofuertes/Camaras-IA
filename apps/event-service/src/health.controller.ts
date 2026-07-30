import { Controller, Get } from '@nestjs/common';
import { CONTRACTS_VERSION } from '@percepta/contracts';

@Controller('health')
export class HealthController {
  @Get()
  health(): { ok: boolean; service: string; contractsVersion: string } {
    return { ok: true, service: 'event-service', contractsVersion: CONTRACTS_VERSION };
  }
}
