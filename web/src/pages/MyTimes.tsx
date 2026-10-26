import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { dragState, paint, setDay, ticked } from "../availability";
import { capitaliseFirst, time } from "../format";
import { confirm } from "../telegram";
import { dayLabel, rowTimes } from "../heatmap";
import type { MyGrid } from "../types";
import { forget, useLoad } from "../useLoad";

/** A member's own times for a poll: busy cells are blocked, the rest can be ticked or cleared. */
export function MyTimes() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  // FindTime sends the organiser here right after posting the poll.
  const justPosted = Boolean((useLocation().state as { justPosted?: boolean } | null)?.justPosted);
  const { data, error } = useLoad(() => api.myGrid(id), [id]);
  const [grid, setGrid] = useState<MyGrid | null>(null);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const drag = useRef<{ state: "free" | "off"; seen: Set<string> } | null>(null);
  // A pointerdown that painted already changed the cell: the click the browser sends after it must
  // not flip it back. Phones send that click late, so remember when the paint happened.
  const paintedAt = useRef(0);
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
    if (Date.now() - paintedAt.current < 1000) {
      paintedAt.current = 0;
      return;
    }
    const cell = grid.days.flatMap((d) => d.cells).find((c) => c.start_at === start);
    const state = cell ? dragState(cell.state) : null;
    if (state) setGrid((current) => current && paint(current, new Set([start]), state));
  }

  function begin(start: string) {
    paintedAt.current = 0;
    if (!grid) return;
    const cell = grid.days.flatMap((d) => d.cells).find((c) => c.start_at === start);
    const state = cell ? dragState(cell.state) : null;
    if (!state) return;
    drag.current = { state, seen: new Set([start]) };
    paintedAt.current = Date.now();
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
    if (
      ticked(grid).length === 0 &&
      !(await confirm("You haven't marked any free times. Submit anyway? You'll be counted as busy the whole time."))
    )
      return;
    setSaving(true);
    setStatus("");
    try {
      const done = await api.saveGrid(id, ticked(grid));
      // The poll and group pages now show you as counted in: don't flash the old copy.
      forget(`poll:${id}`);
      forget("group:");
      const note = `Saved. You're counted in.${done.dropped ? " A few times clashed with your calendar and were left out." : ""}`;
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
      {justPosted && <p className="banner ok">Poll posted in the group ✓ Now add your own free times. You count too.</p>}
      <p className="instruction">Tap the times you're <strong>free</strong>.</p>
      <p className="hint">
        Tap a slot or drag across several. Tap again to undo. Times your calendar or timetable says you're busy are
        already blocked.
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
        }}
        onPointerMove={move}
        onPointerLeave={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        <div className="heat-grid" style={{ gridTemplateColumns: `3.2rem repeat(${grid.days.length}, minmax(22%, 1fr))` }}>
          <div className="heat-corner" />
          {grid.days.map((day) => {
            const open = day.cells.filter((c) => c.state !== "busy");
            const full = open.length > 0 && open.every((c) => c.state === "free");
            return (
              <div key={day.date} className="heat-head">
                {dayLabel(day.date)}
                {open.length > 0 && (
                  <button
                    type="button"
                    className={`day-fill${full ? " on" : ""}`}
                    aria-pressed={full}
                    aria-label={`${full ? "Clear" : "Free all day on"} ${dayLabel(day.date)}`}
                    onClick={() => setGrid((current) => current && setDay(current, day.date, full ? "off" : "free"))}
                  >
                    {full ? "Clear" : "Free all day"}
                  </button>
                )}
              </div>
            );
          })}
          {rows.map((time) => (
            <Row key={time} time={time} grid={grid} lookup={lookup} begin={begin} over={over} tap={tap} />
          ))}
        </div>
      </div>
      {status && <p className="error" role="alert">{status}</p>}
      {/* Pinned to the bottom: the grid is long, and people missed a Submit below it. */}
      <div className="submit-bar">
        <p>{freeHours(ticked(grid).length)}</p>
        <button type="button" className="on big" disabled={saving} onClick={save}>
          {saving ? "Submitting…" : "Submit availabilities"}
        </button>
      </div>
    </section>
  );
}

/** "6 hrs free" from a count of ticked half hours. */
export function freeHours(halves: number): string {
  if (halves === 0) return "No free times marked yet";
  const hours = halves / 2;
  return `${hours} hr${hours === 1 ? "" : "s"} free`;
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
