import { useState } from "react";
import { dayLabel } from "../heatmap";
import { shiftKey, startOfDay, todayKey } from "../format";

const monthLabel = new Intl.DateTimeFormat("en-SG", { month: "long", year: "numeric", timeZone: "Asia/Singapore" });
const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Tap individual dates; the server and availability grid support a 14-day window. */
export function MultiDatePicker({ value, onChange }: { value: string[]; onChange: (dates: string[]) => void }) {
  const today = todayKey();
  const [month, setMonth] = useState(() => (value[0] ?? today).slice(0, 7) + "-01");
  const first = startOfDay(month);
  // Singapore midnight is the previous UTC date: use local noon for the weekday.
  const firstWeekday = (new Date(`${month}T12:00:00+08:00`).getUTCDay() + 6) % 7;
  const next = shiftKey(month, 32).slice(0, 7) + "-01";
  const days = Math.round((startOfDay(next).getTime() - first.getTime()) / 86400000);
  const selected = [...value].sort();
  const previousMonth = shiftKey(month, -1).slice(0, 7) + "-01";

  function choose(key: string) {
    onChange(value.includes(key) ? value.filter((date) => date !== key) : [...value, key].sort());
  }

  return (
    <div className="card date-picker" role="group" aria-label="Choose dates">
      <p className="hint" id="choose-dates-help">Tap dates to select or deselect. Choose dates within a 14-day window.</p>
      <div className="date-picker-month">
        <button type="button" aria-label="Previous month" disabled={previousMonth < today.slice(0, 7) + "-01"} onClick={() => setMonth(previousMonth)}>‹</button>
        <strong aria-live="polite">{monthLabel.format(first)}</strong>
        <button type="button" aria-label="Next month" onClick={() => setMonth(next)}>›</button>
      </div>
      <div className="date-picker-grid" aria-describedby="choose-dates-help">
        {weekdays.map((name) => <span key={name} className="date-picker-weekday" aria-hidden="true">{name}</span>)}
        {Array.from({ length: firstWeekday }, (_, i) => <span key={`blank-${i}`} />)}
        {Array.from({ length: days }, (_, i) => {
          const key = shiftKey(month, i);
          const on = value.includes(key);
          const outside = selected.length > 0 && (key < shiftKey(selected[selected.length - 1], -13) || key > shiftKey(selected[0], 13));
          return (
            <button type="button" key={key} className={on ? "on" : ""} aria-label={dayLabel(key)} aria-pressed={on}
              disabled={key < today || (!on && outside)} onClick={() => choose(key)}>{i + 1}</button>
          );
        })}
      </div>
      <p className="hint date-picker-selection" aria-live="polite">
        {selected.length ? `Selected: ${selected.map(dayLabel).join(", ")}` : "Select at least one date."}
      </p>
    </div>
  );
}
