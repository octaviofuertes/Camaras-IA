// Evaluación de ventanas horarias por zona horaria de la sucursal.
// La config viene de camera_module_configs.config.schedule (CONTRACTS §7).

export type Day = 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun';

export interface ModuleSchedule {
  days?: Day[];
  /** Ventanas [inicio, fin] en HH:MM local. Una ventana con fin < inicio cruza medianoche. */
  windows?: [string, string][];
  tz?: string;
}

const DAY_MAP: Record<string, Day> = {
  Mon: 'mon', Tue: 'tue', Wed: 'wed', Thu: 'thu', Fri: 'fri', Sat: 'sat', Sun: 'sun',
};

function localParts(at: Date, tz: string): { day: Day; minutes: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(at);
  const get = (type: string): string => parts.find((p) => p.type === type)?.value ?? '';
  // Intl puede devolver "24" para medianoche con hour12:false en algunos ICU
  const hour = Number(get('hour')) % 24;
  return {
    day: DAY_MAP[get('weekday')],
    minutes: hour * 60 + Number(get('minute')),
  };
}

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

/**
 * ¿La regla está activa en el instante `at`?
 * Un schedule vacío/ausente significa "siempre activa".
 * Nota: en ventanas que cruzan medianoche, el día se evalúa sobre el día local
 * del instante consultado.
 */
export function isWithinSchedule(schedule: ModuleSchedule | undefined, at: Date): boolean {
  if (!schedule || (!schedule.days?.length && !schedule.windows?.length)) return true;

  const { day, minutes } = localParts(at, schedule.tz ?? 'UTC');

  if (schedule.days?.length && !schedule.days.includes(day)) return false;
  if (!schedule.windows?.length) return true;

  return schedule.windows.some(([start, end]) => {
    const s = toMinutes(start);
    const e = toMinutes(end);
    if (s === e) return true;              // ventana de 24h
    if (s < e) return minutes >= s && minutes < e;
    return minutes >= s || minutes < e;    // cruza medianoche
  });
}
