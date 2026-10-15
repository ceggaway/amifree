import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { setDay, ticked, toggle } from "../availability";
import { rowTimes } from "../heatmap";
import type { MyGrid } from "../types";
import { useLoad } from "../useLoad";

const dayLabel = (date: string) =>
  new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
const clock = (iso: string) => {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

/** A member's own times for a poll: busy cells are blocked, the rest can be ticked or cleared. */
export function MyTimes() {
  const { id = "" } = useParams();
  const { data, error } = useLoad(() => api.myGrid(id), [id]);
  const [grid, setGrid] = useState<MyGrid | null>(null);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (data) setGrid(data);
  }, [data]);
  if (error) {
    const hidden = /not found/i.test(error);
    return <p className="error">{hidden ? "This poll isn't available to you." : error}</p>;
  }
  if (!grid) return <p className="hint">Loading…</p>;
  if (grid.closed) {
    return (
      <section>
        <h2>{grid.title}</h2>
        <p className="hint">This poll is closed.</p>
      </section>
    );
  }
  const rows = rowTimes(grid.days.map((d) => ({ date: d.date, cells: d.cells.map((c) => ({ ...c, free: 0 })) })));
  const lookup = grid.days.map((d) => new Map(d.cells.map((c) => [clock(c.start_at), c])));
  const count = ticked(grid).length;

  async function save() {
    if (!grid) return;
    setSaving(true);
    setStatus("");
    try {
      const done = await api.saveGrid(id, ticked(grid));
      setStatus(`Saved ${done.saved} half hours.${done.dropped ? ` ${done.dropped} were blocked and left out.` : ""}`);
    } catch (e) {
      setStatus((e as Error).message || "Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="heatmap">
      <h2>{grid.title}</h2>
      <p className="hint">
        {grid.known
          ? "We've blocked the times you're busy. Untick any other times that don't work."
          : "We don't have your schedule yet, so tap the times you're free."}{" "}
        The group only sees how many people are free, not who.
      </p>
      <div className="heat-scroll">
        <div className="heat-grid" style={{ gridTemplateColumns: `3.2rem repeat(${grid.days.length}, minmax(22%, 1fr))` }}>
          <div />
          {grid.days.map((day) => (
            <div key={day.date} className="heat-head">
              {dayLabel(day.date)}
              <div className="heat-actions">
                <button type="button" onClick={() => setGrid(setDay(grid, day.date, "free"))}>all</button>
                <button type="button" onClick={() => setGrid(setDay(grid, day.date, "off"))}>none</button>
              </div>
            </div>
          ))}
          {rows.map((time) => (
            <Row key={time} time={time} grid={grid} lookup={lookup} onTap={(s) => setGrid(toggle(grid, s))} />
          ))}
        </div>
      </div>
      <p className="hint" aria-live="polite">
        {status || `${count} half hours ticked.`}
      </p>
      <button type="button" className="on" disabled={saving} onClick={save}>
        {saving ? "Saving…" : grid.submitted ? "Update my times" : "Submit my times"}
      </button>
    </section>
  );
}

function Row({
  time,
  grid,
  lookup,
  onTap,
}: {
  time: string;
  grid: MyGrid;
  lookup: Map<string, MyGrid["days"][number]["cells"][number]>[];
  onTap: (start: string) => void;
}) {
  return (
    <>
      <div className="heat-time">{time.endsWith(":00") ? time : ""}</div>
      {grid.days.map((day, index) => {
        const cell = lookup[index].get(time);
        if (!cell) return <div key={day.date} className="heat-cell gone" />;
        const label = `${dayLabel(day.date)} ${time}: ${cell.state === "busy" ? "busy" : cell.state === "free" ? "free" : "not free"}`;
        return (
          <button
            key={day.date}
            type="button"
            className={`heat-cell mine ${cell.state}`}
            disabled={cell.state === "busy"}
            aria-pressed={cell.state === "free"}
            aria-label={label}
            onClick={() => onTap(cell.start_at)}
          >
            {cell.state === "free" ? "✓" : cell.state === "busy" ? "–" : ""}
          </button>
        );
      })}
    </>
  );
}
