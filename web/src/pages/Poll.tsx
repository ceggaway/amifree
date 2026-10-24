import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { day, span } from "../format";
import { confirm, haptic } from "../telegram";
import type { PollAnswer, PollDetail } from "../types";
import { useLoad } from "../useLoad";
import { Heatmap } from "./Heatmap";

/** A find-a-time poll: join, answer each suggested time, and (organisers) settle it. */
export function Poll() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const loaded = useLoad(() => api.poll(id), [id]);
  const [poll, setPoll] = useState<PollDetail | null>(null);
  // MyTimes sends a note back here after a save.
  const location = useLocation();
  const [note, setNote] = useState((location.state as { note?: string } | null)?.note ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const p = poll ?? loaded.data;

  if (loaded.error) {
    const hidden = /not found/i.test(loaded.error);
    return <p className="error">{hidden ? "This poll isn't available to you." : loaded.error}</p>;
  }
  if (!p) return <p className="hint">Loading…</p>;

  async function run(work: () => Promise<PollDetail | { message: string }>) {
    setError("");
    setBusy(true);
    try {
      const result = await work();
      if ("message" in result) {
        setNote(result.message);
        setPoll(null);
        loaded.reload();
      } else setPoll(result);
      haptic("success");
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function pick(slot: number) {
    if (!(await confirm("Post this time as the event? The poll closes for everyone."))) return;
    setBusy(true);
    try {
      const { event_id } = await api.pick(p!.id, slot);
      haptic("success");
      navigate(`/e/${event_id}?posted=1`);
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
      setBusy(false);
    }
  }

  const hours = p.duration_minutes / 60;
  return (
    <>
      <p>
        <Link to={`/g/${p.chat}`}>‹ All events</Link>
      </p>
      <h1>{p.title}</h1>
      <p className="hint">
        {Number.isInteger(hours * 2) ? `${hours}h` : `${p.duration_minutes} min`}
        {p.deadline_at && p.status !== "CLOSED" ? ` · reply by ${day(p.deadline_at)}` : ""}
      </p>
      {note && <p className="banner ok">{note}</p>}
      {error && <p className="banner clash">{error}</p>}

      {p.status === "CLOSED" && <p className="banner info">This poll is closed. The event is in the group.</p>}

      {p.status === "COLLECTING" && (
        <section className="card">
          <p>
            {p.joined} joined. Times are suggested from everyone's schedules when the reply time passes
            {p.joined < p.min_joined ? ` (at least ${p.min_joined} need to join)` : ""}.
          </p>
          <p className="hint">The group only sees how many people are free, never who is busy or why.</p>
          {p.joined_by_me && <p className="banner ok">You're in.</p>}
          {/* Submitting the grid also counts the member in. */}
          <button type="button" className="on big" onClick={() => navigate(`/poll/${p.id}/mine`)}>
            {p.joined_by_me ? "Update my availabilities" : "Fill in my availabilities"}
          </button>
        </section>
      )}

      {p.status === "OPEN" &&
        p.slots.map((slot, index) => (
          <section key={slot.start_at} className="card">
            <div className="row between">
              <h3>
                {index + 1}. {day(slot.start_at)} · {span(slot.start_at, slot.end_at)}
              </h3>
            </div>
            <p className="hint">
              {slot.yes} vote{slot.yes === 1 ? "" : "s"} · {slot.free} of {slot.of} have no known clash
            </p>
            {(() => {
              // One vote each: voting here moves it from any other time; tapping again takes it back.
              const mine = p.mine[String(index)] === "FREE";
              const answer: PollAnswer = mine ? "BUSY" : "FREE";
              return (
                <button
                  type="button"
                  className={`big${mine ? " on" : ""}`}
                  aria-pressed={mine}
                  disabled={busy}
                  onClick={() => run(() => api.vote(p.id, index, answer))}
                >
                  {mine ? "✓ Your vote" : "Vote for this time"}
                </button>
              );
            })()}
            {p.organiser && (
              <button type="button" disabled={busy} onClick={() => pick(index)}>
                Pick this time
              </button>
            )}
          </section>
        ))}

      {p.organiser && p.status !== "CLOSED" && (
        <section className="card organiser">
          <h2>You're the organiser</h2>
          <div className="row">
            {p.status === "COLLECTING" && (
              <button type="button" className="on" disabled={busy} onClick={() => run(() => api.suggest(p.id))}>
                Suggest times now
              </button>
            )}
            <button type="button" disabled={busy} onClick={() => run(() => api.nudgePoll(p.id))}>
              Nudge
            </button>
          </div>
          {p.status === "OPEN" && <p className="hint">Pick a time above to post it as an event card.</p>}
        </section>
      )}

      {p.joined_by_me && p.status !== "CLOSED" && (
        <details>
          <summary>Who's free when</summary>
          <Heatmap />
        </details>
      )}
    </>
  );
}
