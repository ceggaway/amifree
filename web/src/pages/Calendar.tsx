import { useState } from "react";
import { api } from "../api";
import { addDays, day, dayKey, span, startOfDay } from "../format";
import { useLoad } from "../useLoad";

/** Monday of the current week, Singapore time. */
function thisMonday(): Date {
  const today = startOfDay(dayKey(new Date()));
  const weekday = new Date(today.getTime() + 8 * 3600000).getUTCDay();
  return addDays(today, -((weekday + 6) % 7));
}

export function Calendar() {
  const [start, setStart] = useState(thisMonday);
  const { data, error } = useLoad(() => api.calendar(start, addDays(start, 7)), [start.getTime()], `calendar:${start.getTime()}`);
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  return (
    <section>
      <div className="row between">
        <button onClick={() => setStart(addDays(start, -7))}>‹ Previous</button>
        <h2>Week of {day(start)}</h2>
        <button onClick={() => setStart(addDays(start, 7))}>Next ›</button>
      </div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="hint">Loading…</p>}
      {data &&
        days.map((date) => {
          const key = dayKey(date);
          const items = [
            ...data.blocks.map((b) => ({ ...b, kind: b.source === "MANUAL" ? "Busy" : "Class", status: "" })),
            ...data.plans.map((p) => ({ ...p, kind: "Plan" })),
          ]
            .filter((item) => dayKey(item.start_at) === key)
            .sort((a, b) => a.start_at.localeCompare(b.start_at));
          return (
            <div key={key} className="day">
              <h3>{day(date)}</h3>
              {items.length === 0 && <p className="hint">Free</p>}
              {items.map((item, i) => (
                <div key={i} className={`slot ${item.kind.toLowerCase()}`}>
                  <span>{span(item.start_at, item.end_at)}</span>
                  <span>
                    {item.title}
                    {item.location ? ` · ${item.location}` : ""}
                  </span>
                  <span className="hint">{item.kind === "Plan" ? item.status.toLowerCase() : item.kind}</span>
                </div>
              ))}
            </div>
          );
        })}
    </section>
  );
}
