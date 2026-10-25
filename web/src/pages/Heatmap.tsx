import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { day as dayText, time, time as timeOf } from "../format";
import { bestCell, dayLabel, dayLookup, meetingFree, rowTimes, shade } from "../heatmap";
import { confirm, haptic } from "../telegram";
import type { Heatmap as HeatmapData } from "../types";
import { useLoad } from "../useLoad";

type Picked = { day: number; start_at: string };

/** How many members are free at each half hour. Counts only: nobody's name or reason is shown.
 * The organiser can tap any time and choose it for the meeting. */
export function Heatmap() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data, error } = useLoad(() => api.pollHeatmap(id), [id]);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [problem, setProblem] = useState("");
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
          {data.min_known} are needed before the grid is shown.
        </p>
      </section>
    );
  }
  const rows = rowTimes(data.days);
  const lookups = data.days.map(dayLookup);
  const best = bestCell(data.days);
  const bestText = best ? `${dayText(best.start_at)} ${time(best.start_at)}` : "";
  const canChoose = data.organiser && data.status !== "CLOSED";
  const minutes = data.duration_minutes;
  const free = picked ? meetingFree(data.days[picked.day], picked.start_at, minutes) : null;
  const end = picked ? new Date(Date.parse(picked.start_at) + minutes * 60 * 1000).toISOString() : "";
  const when = picked ? `${dayText(picked.start_at)}, ${time(picked.start_at)}–${time(end)}` : "";

  async function choose() {
    if (!picked) return;
    if (!(await confirm(`Choose ${when}? It's posted in the group and the poll closes.`))) return;
    setChoosing(true);
    setProblem("");
    try {
      const { event_id } = await api.pickTime(id, picked.start_at);
      haptic("success");
      navigate(`/e/${event_id}?posted=1`);
    } catch (err) {
      haptic("error");
      setProblem(refusal(err).message);
      setChoosing(false);
    }
  }
  return (
    <section className="heatmap">
      <h2>{data.title}</h2>
      <p className="hint">
        {data.known} schedules checked. Darker means more people are free. Only counts are shown,
        never who is busy.{canChoose ? " Tap any time to choose it for the meeting." : ""}
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
            <Row key={time} time={time} data={data} lookups={lookups} picked={picked} onPick={setPicked} />
          ))}
        </div>
      </div>
      <div aria-live="polite">
        {!picked ? (
          <p className="hint">Tap a time to see how many are free.</p>
        ) : free === null ? (
          <p className="hint">
            {when}: the meeting would run past the last time that can be checked. Pick an earlier time.
          </p>
        ) : (
          <div className="card choose-bar">
            <p>
              <b>{when}</b> · {free} of {data.max_free} free for the whole meeting
            </p>
            {canChoose && (
              <button type="button" className="on" disabled={choosing} onClick={choose}>
                {choosing ? "Choosing…" : "Choose this time"}
              </button>
            )}
          </div>
        )}
        {problem && <p className="error">{problem}</p>}
      </div>
      <Link to={`/poll/${id}/mine`}>Set my own times</Link>
    </section>
  );
}

function Row({
  time,
  data,
  lookups,
  picked,
  onPick,
}: {
  time: string;
  data: HeatmapData;
  lookups: Map<string, number>[];
  picked: Picked | null;
  onPick: (picked: Picked) => void;
}) {
  return (
    <>
      <div className={`heat-time${time.endsWith(":00") ? " hour" : ""}`}>{time}</div>
      {data.days.map((day, index) => {
        const free = lookups[index].get(time);
        const cell = day.cells.find((c) => timeOf(c.start_at) === time);
        if (free === undefined || !cell) return <div key={day.date} className="heat-cell gone" />;
        const selected = picked?.start_at === cell.start_at;
        return (
          <button
            key={day.date}
            type="button"
            className={`heat-cell${selected ? " selected" : ""}`}
            aria-pressed={selected}
            style={{ opacity: 0.15 + 0.85 * shade(free, data.max_free) }}
            aria-label={`${dayLabel(day.date)} ${time}: ${free} of ${data.max_free} free`}
            onClick={() => onPick({ day: index, start_at: cell.start_at })}
          >
            {free}
          </button>
        );
      })}
    </>
  );
}
