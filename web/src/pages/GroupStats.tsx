import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { haptic } from "../telegram";
import { useLoad } from "../useLoad";
import { NotInGroup } from "./GroupHome";

const PERIODS: [number, string][] = [
  [30, "30 days"],
  [120, "4 months"],
  [365, "1 year"],
];

/** Turnout for the events you organised in this group, and the attendance CSV. */
export function GroupStats() {
  const { code = "" } = useParams();
  const [days, setDays] = useState(120);
  const { data, error } = useLoad(() => api.groupStats(code, days), [code, days]);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [sending, setSending] = useState(false);

  async function send() {
    setNote("");
    setProblem("");
    setSending(true);
    try {
      setNote((await api.exportAttendance(code, days)).message);
      haptic("success");
    } catch (err) {
      haptic("error");
      setProblem(refusal(err).message);
    } finally {
      setSending(false);
    }
  }

  if (error) return <NotInGroup error={error} />;
  return (
    <>
      <p>
        <Link to={`/g/${code}`}>‹ All events</Link>
      </p>
      <h1>Turnout</h1>
      <p className="hint">Events you organised in this group.</p>
      <div className="segmented" role="group" aria-label="Period">
        {PERIODS.map(([value, label]) => (
          <button key={value} type="button" className={days === value ? "on" : ""} aria-pressed={days === value} onClick={() => setDays(value)}>
            {label}
          </button>
        ))}
      </div>
      {!data ? (
        <p className="hint">Loading…</p>
      ) : data.held === 0 ? (
        <p className="hint">No finished events of yours in this period. Stats appear once one ends.</p>
      ) : (
        <>
          <section className="card stat-row">
            <div>
              <strong>{data.turnout}</strong>
              <span className="hint">turnout</span>
            </div>
            <div>
              <strong>{data.came}</strong>
              <span className="hint">came of {data.going} going</span>
            </div>
            <div>
              <strong>{data.held}</strong>
              <span className="hint">held{data.cancelled ? ` · ${data.cancelled} cancelled` : ""}</span>
            </div>
          </section>
          {data.by_event.length > 0 && (
            <>
              <h2>By event</h2>
              {data.by_event.map((row, index) => (
                <div key={index} className="card row between">
                  <span>
                    {row.title}
                    <span className="hint"> · {row.sessions} session{row.sessions === 1 ? "" : "s"}</span>
                  </span>
                  <strong>{row.turnout}</strong>
                </div>
              ))}
            </>
          )}
          {data.pro_required && <p className="banner info">The per-event breakdown and CSV are part of Clubs Pro. Send /pro in the group.</p>}
          <p className="hint">"Came" counts people who tapped I'm here or whom you marked on the event page.</p>
        </>
      )}

      <h2>Attendance file</h2>
      <p className="hint">A spreadsheet of everyone who answered: RSVP, still coming?, and whether they came.</p>
      <button type="button" className="on big" disabled={sending || data?.pro_required} onClick={send}>
        Send CSV to my chat
      </button>
      {note && <p className="banner ok">{note}</p>}
      {problem && <p className="banner clash">{problem}</p>}
    </>
  );
}
