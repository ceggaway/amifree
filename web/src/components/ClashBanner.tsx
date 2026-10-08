import { span } from "../format";
import type { Availability } from "../types";

/** Mirrors the first line of the bot's cards, from the same server-side clash engine. */
export function ClashBanner({ availability }: { availability: Availability }) {
  const { conflicts, possible, busy_day, day_count, covered } = availability;
  return (
    <div className="banner-stack">
      {conflicts.length > 0 ? (
        <div className="banner clash">
          ⚠️ Clashes with {conflicts[0].title} ({span(conflicts[0].start_at, conflicts[0].end_at)})
          {conflicts.length > 1 ? ` and ${conflicts.length - 1} more` : ""}
        </div>
      ) : busy_day ? (
        <div className="banner info">ℹ️ Busy day: {day_count} other plans, no clashes</div>
      ) : (
        <div className="banner ok">✅ No clashes found</div>
      )}
      {possible.length > 0 && (
        <div className="banner info">
          Possible overlap with {possible.map((p) => p.title).join(", ")}. Choose which to attend.
        </div>
      )}
      {!covered && <div className="hint">No NUSMods timetable covers this date.</div>}
    </div>
  );
}
