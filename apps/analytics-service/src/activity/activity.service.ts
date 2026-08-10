import { BadRequestException, Injectable } from '@nestjs/common';
import { DatabaseService } from '../db/database.service';
import {
  ActivityRepository,
  type FilaInforme,
  type FiltroInforme,
  type MuestraEntrada,
} from './activity.repository';
import type { AuthContext } from '../auth/auth.types';

export interface ResumenPuesto {
  cameraId: string;
  zoneId: string | null;
  zoneName: string;
  observadoSegundos: number;
  ocupadoSegundos: number;
  telefonoSegundos: number;
  vacioSegundos: number;
  sinCoberturaSegundos: number;
  /** Porcentaje del tiempo OBSERVADO, no del tiempo transcurrido. */
  ocupacionPct: number;
  telefonoPct: number;
  /** Qué fracción del rango pedido llegó a observarse. */
  coberturaPct: number;
  ocupacionMedia: number;
  maxPersonas: number;
}

export interface Informe {
  desde: string;
  hasta: string;
  puestos: ResumenPuesto[];
  serie: FilaInforme[];
  total: ResumenPuesto | null;
  advertencias: string[];
}

@Injectable()
export class ActivityService {
  constructor(
    private readonly db: DatabaseService,
    private readonly repo: ActivityRepository,
  ) {}

  async ingest(auth: AuthContext, m: Omit<MuestraEntrada, 'organizationId'>): Promise<string | null> {
    if (!(m.to > m.from)) {
      throw new BadRequestException('la ventana medida debe terminar después de empezar');
    }
    return this.db.withTenant(auth.organizationId, (c) =>
      this.repo.insertar(c, { ...m, organizationId: auth.organizationId }),
    );
  }

  /**
   * Arma el informe de un rango.
   *
   * Combina el agregado continuo (rápido, hasta hace unos minutos) con las
   * muestras crudas del tramo más reciente. Sin esa combinación, alguien que
   * mira el informe mientras la cámara está grabando lo vería congelado y
   * concluiría —con razón— que no funciona.
   */
  async informe(auth: AuthContext, f: FiltroInforme): Promise<Informe> {
    const desde = new Date(f.desde);
    const hasta = new Date(f.hasta);
    if (Number.isNaN(desde.getTime()) || Number.isNaN(hasta.getTime()) || desde >= hasta) {
      throw new BadRequestException('rango de fechas inválido');
    }

    return this.db.withTenant(auth.organizationId, async (c) => {
      const [puestos, serie, ultimaHora] = await Promise.all([
        this.repo.porPuesto(c, f),
        this.repo.desglose(c, f),
        this.repo.ultimaHoraAgregada(c),
      ]);

      // Completar con lo que todavía no agregó Timescale.
      const corte = ultimaHora ? new Date(new Date(ultimaHora).getTime() + 3600_000) : desde;
      const inicioReciente = corte > desde ? corte : desde;
      const recientes =
        inicioReciente < hasta ? await this.repo.recientes(c, inicioReciente.toISOString()) : [];

      const combinados = fusionar([...puestos, ...recientes.filter((r) => r.windowSeconds > 0)]);

      const rango = (hasta.getTime() - desde.getTime()) / 1000;
      const resumen = combinados.map((r) => aResumen(r, rango));
      const total = totalizar(resumen);

      return {
        desde: desde.toISOString(),
        hasta: hasta.toISOString(),
        puestos: resumen,
        serie,
        total,
        advertencias: advertir(resumen),
      };
    });
  }
}

/** Suma filas del mismo puesto que vinieron de fuentes distintas. */
function fusionar(filas: FilaInforme[]): FilaInforme[] {
  const por = new Map<string, FilaInforme>();
  for (const f of filas) {
    const k = `${f.cameraId}|${f.zoneId ?? ''}`;
    const acc = por.get(k);
    if (!acc) {
      por.set(k, { ...f });
      continue;
    }
    const observadoA = acc.occupiedSeconds + acc.emptySeconds;
    const observadoB = f.occupiedSeconds + f.emptySeconds;
    acc.windowSeconds += f.windowSeconds;
    acc.occupiedSeconds += f.occupiedSeconds;
    acc.phoneSeconds += f.phoneSeconds;
    acc.emptySeconds += f.emptySeconds;
    acc.uncoveredSeconds += f.uncoveredSeconds;
    acc.maxPeople = Math.max(acc.maxPeople, f.maxPeople);
    const totalObs = observadoA + observadoB;
    acc.meanOccupancy =
      totalObs > 0
        ? (acc.meanOccupancy * observadoA + f.meanOccupancy * observadoB) / totalObs
        : 0;
    if (!acc.zoneName) acc.zoneName = f.zoneName;
  }
  return [...por.values()].sort((a, b) => b.occupiedSeconds - a.occupiedSeconds);
}

function aResumen(f: FilaInforme, rangoSegundos: number): ResumenPuesto {
  // Los porcentajes se calculan sobre el tiempo OBSERVADO, no sobre el
  // transcurrido. Si la cámara estuvo caída la mitad del turno, decir "50 % de
  // ocupación" sería mentir por omisión: lo honesto es decir que de lo que se
  // vio, tanto estuvo ocupado, y mostrar aparte cuánto se vio.
  const observado = f.occupiedSeconds + f.emptySeconds;
  const pct = (parte: number, total: number): number =>
    total > 0 ? Math.round((parte / total) * 1000) / 10 : 0;

  return {
    cameraId: f.cameraId,
    zoneId: f.zoneId,
    zoneName: f.zoneName || 'Toda la cámara',
    observadoSegundos: Math.round(observado),
    ocupadoSegundos: Math.round(f.occupiedSeconds),
    telefonoSegundos: Math.round(f.phoneSeconds),
    vacioSegundos: Math.round(f.emptySeconds),
    sinCoberturaSegundos: Math.round(f.uncoveredSeconds),
    ocupacionPct: pct(f.occupiedSeconds, observado),
    telefonoPct: pct(f.phoneSeconds, f.occupiedSeconds),
    coberturaPct: pct(observado, rangoSegundos),
    ocupacionMedia: Math.round(f.meanOccupancy * 100) / 100,
    maxPersonas: f.maxPeople,
  };
}

function totalizar(puestos: ResumenPuesto[]): ResumenPuesto | null {
  if (!puestos.length) return null;
  const s = (sel: (p: ResumenPuesto) => number): number => puestos.reduce((a, p) => a + sel(p), 0);
  const observado = s((p) => p.observadoSegundos);
  const ocupado = s((p) => p.ocupadoSegundos);
  const pct = (parte: number, total: number): number =>
    total > 0 ? Math.round((parte / total) * 1000) / 10 : 0;

  return {
    cameraId: '',
    zoneId: null,
    zoneName: 'Todos los puestos',
    observadoSegundos: observado,
    ocupadoSegundos: ocupado,
    telefonoSegundos: s((p) => p.telefonoSegundos),
    vacioSegundos: s((p) => p.vacioSegundos),
    sinCoberturaSegundos: s((p) => p.sinCoberturaSegundos),
    ocupacionPct: pct(ocupado, observado),
    telefonoPct: pct(s((p) => p.telefonoSegundos), ocupado),
    // La cobertura del conjunto se promedia, no se suma: sumarla daría más de
    // 100 % con varios puestos.
    coberturaPct: Math.round((s((p) => p.coberturaPct) / puestos.length) * 10) / 10,
    ocupacionMedia: Math.round((s((p) => p.ocupacionMedia)) * 100) / 100,
    maxPersonas: Math.max(...puestos.map((p) => p.maxPersonas)),
  };
}

/**
 * Advertencias que viajan CON el informe.
 *
 * Un informe que presenta números sin decir cuánto se puede confiar en ellos es
 * peor que no tenerlo: alguien va a tomar una decisión sobre una persona con un
 * porcentaje que no aguanta el peso que le van a poner encima.
 */
function advertir(puestos: ResumenPuesto[]): string[] {
  const avisos: string[] = [];

  const flojos = puestos.filter((p) => p.coberturaPct < 80);
  if (flojos.length) {
    avisos.push(
      `Cobertura parcial en ${flojos.length} puesto(s): ` +
        flojos.map((p) => `${p.zoneName} (${p.coberturaPct}%)`).join(', ') +
        '. Los porcentajes son sobre el tiempo observado, no sobre el rango completo.',
    );
  }

  if (puestos.some((p) => p.telefonoSegundos > 0)) {
    avisos.push(
      'El tiempo de teléfono es una COTA INFERIOR: sólo se cuenta cuando el ' +
        'teléfono se ve. Si queda tapado por el cuerpo o la persona está de ' +
        'espaldas, no se detecta. Sirve para comparar entre puestos y franjas, ' +
        'no como medida absoluta.',
    );
  }

  avisos.push(
    'La medición es por puesto de trabajo. No identifica personas ni permite ' +
      'reconstruir quién ocupó cada posición.',
  );
  return avisos;
}
