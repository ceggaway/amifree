import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, refusal } from "../api";
import { day as dayText, time } from "../format";
import { dayLabel, meetingFree } from "../heatmap";
import { confirm, haptic } from "../telegram";
import { useLoad } from "../useLoad";

/** The organiser picks a meeting time by hand: a day, then a start time with how many are free. */
export function ChooseTime({ pollId }: { pollId: string }) {
  const navigate = useNavigate();
  const { data, error } = useLoad(() => api.pollHeatmap(pollId), [pollId]);
  const [dayIndex, setDayIndex] = useState(0);
  const [start, setStart] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  if (error || !data) {
    return error ? <p className="hint">Fill in your own availability first to choose a time here.</p> : null;
  }
  if (!data.ready) {
    return (
      <p className="hint">
        Once at least {data.min_known} people have filled in their availability, you can choose any time here.
      </p>
    );
  }
  const minutes = data.duration_minutes;
  const day = data.days[Math.min(dayIndex, data.days.length - 1)];
  // Only start times where the whole meeting fits in the day.
  const options = day
    ? day.cells
        .map((cell) => ({ cell, free: meetingFree(day, cell.start_at, minutes) }))
        .filter((option) => option.free !== null)
    : [];
  const chosen = options.find((option) => option.cell.start_at === start) ?? options[0];
  const endOf = (startAt: string) => new Date(Date.parse(startAt) + minutes * 60 * 1000).toISOString();

  async function submit() {
    if (!chosen) return;
    const when = `${dayText(chosen.cell.start_at)}, ${time(chosen.cell.start_at)}–${time(endOf(chosen.cell.start_at))}`;
    if (!(await confirm(`Confirm ${when}? It will be announced in the group and the poll closes.`))) return;
    setBusy(true);
    setProblem("");
    try {
      const { event_id } = await api.pickTime(pollId, chosen.cell.start_at);
      haptic("success");
      navigate(`/e/${event_id}?posted=1`);
    } catch (err) {
      haptic("error");
      setProblem(refusal(err).message);
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <label>
        Day
        <select
          value={dayIndex}
          onChange={(e) => {
            setDayIndex(Number(e.target.value));
            setStart("");
          }}
        >
          {data.days.map((d, index) => (
            <option key={d.date} value={index}>
              {dayLabel(d.date)}
            </option>
          ))}
        </select>
      </label>
      {options.length === 0 ? (
        <p className="hint">No start time on this day leaves room for the whole meeting. Pick another day.</p>
      ) : (
        <label>
          Start time
          <select value={chosen?.cell.start_at ?? ""} onChange={(e) => setStart(e.target.value)}>
            {options.map(({ cell, free }) => (
              <option key={cell.start_at} value={cell.start_at}>
                {time(cell.start_at)}–{time(endOf(cell.start_at))} ({free} of {data.max_free} available)
              </option>
            ))}
          </select>
        </label>
      )}
      {problem && <p className="error">{problem}</p>}
      <button type="button" className="on big" disabled={busy || !chosen} onClick={submit}>
        {busy ? "Confirming…" : "Confirm this time"}
      </button>
    </div>
  );
}
