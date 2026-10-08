// All times are shown in Singapore time, matching the bot.
const zone = "Asia/Singapore";
const dayFormat = new Intl.DateTimeFormat("en-SG", { timeZone: zone, weekday: "short", day: "numeric", month: "short" });
const timeFormat = new Intl.DateTimeFormat("en-SG", { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const keyFormat = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });

export const day = (iso: string | Date) => dayFormat.format(new Date(iso));
export const time = (iso: string) => timeFormat.format(new Date(iso));
export const span = (start: string, end: string) => `${time(start)}–${time(end)}`;
/** YYYY-MM-DD in Singapore, for grouping by day. */
export const dayKey = (iso: string | Date) => keyFormat.format(new Date(iso));

/** Midnight Singapore time for a YYYY-MM-DD key. */
export const startOfDay = (key: string) => new Date(`${key}T00:00:00+08:00`);
export const addDays = (date: Date, days: number) => new Date(date.getTime() + days * 86400000);

/** Value for <input type="datetime-local">, in Singapore time. */
export const toInput = (iso: string) => `${dayKey(iso)}T${time(iso)}`;
export const fromInput = (value: string) => `${value}:00+08:00`;
