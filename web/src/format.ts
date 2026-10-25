// All times are shown in Singapore time, matching the bot.
const zone = "Asia/Singapore";
const dayFormat = new Intl.DateTimeFormat("en-SG", { timeZone: zone, weekday: "short", day: "numeric", month: "short" });
const monthFormat = new Intl.DateTimeFormat("en-SG", { timeZone: zone, month: "short" });
const weekdayFormat = new Intl.DateTimeFormat("en-SG", { timeZone: zone, weekday: "long" });
const dateNumberFormat = new Intl.DateTimeFormat("en-SG", { timeZone: zone, day: "numeric" });
const timeFormat = new Intl.DateTimeFormat("en-SG", { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const keyFormat = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });

export const day = (iso: string | Date) => dayFormat.format(new Date(iso));
export const month = (iso: string) => monthFormat.format(new Date(iso));
export const weekday = (iso: string) => weekdayFormat.format(new Date(iso));
export const dateNumber = (iso: string) => dateNumberFormat.format(new Date(iso));
export const time = (iso: string) => timeFormat.format(new Date(iso));
export const span = (start: string, end: string) => `${time(start)}–${time(end)}`;
/** Capitalise the first letter for display without changing the rest of an event name. */
export const capitaliseFirst = (title: string) => title.trim().replace(/\p{L}/u, (letter) => letter.toUpperCase());
/** YYYY-MM-DD in Singapore, for grouping by day. */
export const dayKey = (iso: string | Date) => keyFormat.format(new Date(iso));

/** Midnight Singapore time for a YYYY-MM-DD key. */
export const startOfDay = (key: string) => new Date(`${key}T00:00:00+08:00`);
export const addDays = (date: Date, days: number) => new Date(date.getTime() + days * 86400000);

/** Value for <input type="datetime-local">, in Singapore time. */
export const toInput = (iso: string) => `${dayKey(iso)}T${time(iso)}`;
// Some browsers include seconds ("…T19:00:00"); keep exactly hours, minutes and seconds.
export const fromInput = (value: string) => `${value.slice(0, 16)}:00+08:00`;

/** "2026-10-23" + "19:00" in Singapore time, as an ISO string with its offset. */
export const at = (dateKey: string, clock: string) => `${dateKey}T${clock}:00+08:00`;
/** Today's date key in Singapore, for date inputs. */
export const todayKey = () => dayKey(new Date());
/** The date key `days` after `key`. */
export const shiftKey = (key: string, days: number) => dayKey(addDays(startOfDay(key), days));
