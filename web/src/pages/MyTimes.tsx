import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { dragState, paint, ticked } from "../availability";
import { capitaliseFirst, time } from "../format";
import { dayLabel, rowTimes } from "../heatmap";
import type { MyGrid } from "../types";
import { useLoad } from "../useLoad";

/** A member's own times for a poll: busy cells are blocked, the rest can be ticked or cleared. */
export function MyTimes() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data, error } = useLoad(() => api.myGrid(id), [id]);
  const [grid, setGrid] = useState<MyGrid | null>(null);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const drag = useRef<{ state: "free" | "off"; seen: Set<string> } | null>(null);
  // A pointerdown that painted already changed the cell: the click that follows must not flip it back.
  const painted = useRef(false);
  useEffect(() => {
    if (data) setGrid(data);
  }, [data]);
  if (error) {
    const hidden = /not found/i.test(error);
    return <p className="error">{hidden ? "This poll isn't available to you." : error}</p>;
  }
  if (!grid) return <p className="hint">Loading…</p>;
  const title = capitaliseFirst(grid.title);
  if (grid.closed) {
    return (
      <section>
        <h1>{title}</h1>
        <p className="hint">This poll is closed.</p>
      </section>
    );
  }
  const rows = rowTimes(grid.days);
  const lookup = grid.days.map((d) => new Map(d.cells.map((c) => [time(c.start_at), c])));

  function tap(start: string) {
    if (!grid) return;
    if (painted.current) {
      painted.current = false;
      return;
    }
    const cell = grid.days.flatMap((d) => d.cells).find((c) => c.start_at === start);
    const state = cell ? dragState(cell.state) : null;
    if (state) setGrid((current) => current && paint(current, new Set([start]), state));
  }

  function begin(start: string) {
    painted.current = false;
    if (!grid) return;
    const cell = grid.days.flatMap((d) => d.cells).find((c) => c.start_at === start);
    const state = cell ? dragState(cell.state) : null;
    if (!state) return;
    drag.current = { state, seen: new Set([start]) };
    painted.current = true;
    setGrid((current) => current && paint(current, new Set([start]), state));
  }

  // Touch: the browser only reports the cell where the finger first landed, so find the cell
  // under the finger as it moves.
  function move(e: React.PointerEvent) {
    if (!drag.current) return;
    const target = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-start]");
    const start = target?.getAttribute("data-start");
    if (start) over(start);
  }

  function over(start: string) {
    const current = drag.current;
    if (!grid || !current || current.seen.has(start)) return;
    current.seen.add(start);
    setGrid((value) => value && paint(value, new Set([start]), current.state));
  }

  async function save() {
    if (!grid) return;
    setSaving(true);
    setStatus("");
    try {
      const done = await api.saveGrid(id, ticked(grid));
      const note = `Saved ${done.saved} half hours.${done.dropped ? ` ${done.dropped} were blocked and left out.` : ""} You're counted in.`;
      // Back to the poll, which now shows "You're in." and the new count.
      navigate(`/poll/${id}`, { replace: true, state: { note } });
    } catch (e) {
      setStatus((e as Error).message || "Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="heatmap">
      <h1>{title}</h1>
      <p className="hint">
        Tap a slot or drag across several to change your availability. Busy times from your connected
        calendar and timetable are blocked automatically.
      </p>
      <div className="grid-legend">
        <span><i className="swatch free" aria-hidden="true">✓</i> Available</span>
        <span><i className="swatch off" aria-hidden="true" /> Unavailable</span>
        <span><i className="swatch busy" aria-hidden="true" /> Calendar / class clash</span>
      </div>
      {grid.from_usual && !grid.submitted && (
        <p className="banner info">Pre-filled from your usual times. Change them with /usual in the bot.</p>
      )}
      <div
        className="heat-scroll availability-scroll"
        onPointerUp={() => {
          drag.current = null;
          // The click that follows a paint is skipped; forget the flag if no click arrives.
          setTimeout(() => (painted.current = false), 0);
        }}
        onPointerMove={move}
        onPointerLeave={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        <div className="heat-grid" style={{ gridTemplateColumns: `3.2rem repeat(${grid.days.length}, minmax(22%, 1fr))` }}>
          <div className="heat-corner" />
          {grid.days.map((day) => (
            <div key={day.date} className="heat-head">
              {dayLabel(day.date)}
            </div>
          ))}
          {rows.map((time) => (
            <Row key={time} time={time} grid={grid} lookup={lookup} begin={begin} over={over} tap={tap} />
          ))}
        </div>
      </div>
      {status && <p className="error" role="alert">{status}</p>}
      <button type="button" className="on" disabled={saving} onClick={save}>
        {saving ? "Submitting…" : "Submit availabilities"}
      </button>
    </section>
  );
}

function Row({
  time,
  grid,
  lookup,
  begin,
  over,
  tap,
}: {
  time: string;
  grid: MyGrid;
  lookup: Map<string, MyGrid["days"][number]["cells"][number]>[];
  begin: (start: string) => void;
  over: (start: string) => void;
  tap: (start: string) => void;
}) {
  return (
    <>
      <div className={`heat-time${time.endsWith(":00") ? " hour" : ""}`}>{time}</div>
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
            data-start={cell.start_at}
            onPointerDown={(e) => {
              if (e.button !== 0) return;
              begin(cell.start_at);
            }}
            onPointerEnter={() => over(cell.start_at)}
            onClick={() => tap(cell.start_at)}
          >
            {cell.state === "free" ? "✓" : cell.state === "busy" ? "–" : ""}
          </button>
        );
      })}
    </>
  );
}
