import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { day as dayText, time } from "../format";
import { bestCell, dayLabel, dayLookup, rowTimes, shade } from "../heatmap";
import type { Heatmap as HeatmapData } from "../types";
import { useLoad } from "../useLoad";

/** How many members are free at each half hour. Counts only: nobody's name or reason is shown. */
export function Heatmap() {
  const { id = "" } = useParams();
  const { data, error } = useLoad(() => api.pollHeatmap(id), [id]);
  const [picked, setPicked] = useState("");
  if (error) {
    const notJoined = /not found/i.test(error);
    return (
      <p className="error">
        {notJoined ? "Only people in this poll can see its grid. Fill in your availabilities on the poll first." : error}
      </p>
    );
  }
  if (!data) return <p className="hint">Loading…</p>;
  if (!data.ready) {
    return (
      <section>
        <h2>{data.title}</h2>
        <p className="hint">
          {data.known} of {data.joined} members have a schedule AmIFree can read. At least{" "}
          {data.min_known} are needed before the grid is shown, so it can't point at one person.
        </p>
      </section>
    );
  }
  const rows = rowTimes(data.days);
  const lookups = data.days.map(dayLookup);
  const best = bestCell(data.days);
  const bestText = best ? `${dayText(best.start_at)} ${time(best.start_at)}` : "";
  return (
    <section className="heatmap">
      <h2>{data.title}</h2>
      <p className="hint">
        {data.known} schedules checked. Darker means more people are free. Only counts are shown,
        never who is busy.
      </p>
      {best && best.free > 0 && (
        <p className="banner ok">
          Most free: {best.free} of {data.max_free} at {bestText}
        </p>
      )}
      <div className="heat-scroll" role="grid" aria-label="Members free by half hour">
        <div className="heat-grid" style={{ gridTemplateColumns: `3.2rem repeat(${data.days.length}, minmax(22%, 1fr))` }}>
          <div className="heat-corner" />
          {data.days.map((day) => (
            <div key={day.date} className="heat-head">
              {dayLabel(day.date)}
            </div>
          ))}
          {rows.map((time) => (
            <Row key={time} time={time} data={data} lookups={lookups} onPick={setPicked} />
          ))}
        </div>
      </div>
      <p className="hint" aria-live="polite">
        {picked || "Tap a cell to see the count."}
      </p>
      <Link to={`/poll/${id}/mine`}>Set my own times</Link>
    </section>
  );
}

function Row({
  time,
  data,
  lookups,
  onPick,
}: {
  time: string;
  data: HeatmapData;
  lookups: Map<string, number>[];
  onPick: (text: string) => void;
}) {
  return (
    <>
      <div className={`heat-time${time.endsWith(":00") ? " hour" : ""}`}>{time}</div>
      {data.days.map((day, index) => {
        const free = lookups[index].get(time);
        if (free === undefined) return <div key={day.date} className="heat-cell gone" />;
        return (
          <button
            key={day.date}
            className="heat-cell"
            style={{ opacity: 0.15 + 0.85 * shade(free, data.max_free) }}
            aria-label={`${dayLabel(day.date)} ${time}: ${free} of ${data.max_free} free`}
            onClick={() => onPick(`${dayLabel(day.date)} ${time}: ${free} of ${data.max_free} free`)}
          >
            {free}
          </button>
        );
      })}
    </>
  );
}
