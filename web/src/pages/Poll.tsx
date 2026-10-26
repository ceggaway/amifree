import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { day, span } from "../format";
import { confirm, haptic } from "../telegram";
import type { PollDetail } from "../types";
import { useLoad } from "../useLoad";
import { ChooseTime } from "../components/ChooseTime";
import { Heatmap } from "./Heatmap";

/** A find-a-time poll: join, answer each suggested time, and (organisers) settle it. */
export function Poll() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const loaded = useLoad(() => api.poll(id), [id], `poll:${id}`);
  // MyTimes sends a note back here after a save.
  const location = useLocation();
  const [note, setNote] = useState((location.state as { note?: string } | null)?.note ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const p = loaded.data;

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
        loaded.reload();
      } else loaded.update(result);
      haptic("success");
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function pick(slot: number) {
    if (!(await confirm("Create an event at this time? This closes the poll and posts the event in the group for RSVPs."))) return;
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
  const fillIn = (
    <button type="button" className="on big" onClick={() => navigate(`/poll/${p.id}/mine`)}>
      {p.joined_by_me ? "Update my availabilities" : "Fill in my availabilities"}
    </button>
  );
  return (
    <>
      <p>
        <Link to={`/g/${p.chat}`}>‹ Back to group</Link>
      </p>
      <h1>{p.title}</h1>
      <p className="hint">
        {Number.isInteger(hours * 2) ? `${hours}h` : `${p.duration_minutes} min`}
        {p.deadline_at && p.status === "COLLECTING" ? ` · fill in by ${day(p.deadline_at)}` : ""}
        {` · ${p.joined} filled in`}
      </p>
      <Steps status={p.status} />
      {note && <p className="banner ok">{note}</p>}
      {error && <p className="banner clash">{error}</p>}

      <section className="card next-step">
        <NextStep poll={p} />
        {p.status === "COLLECTING" && fillIn}
        <p className="hint">The group only sees how many people are free, never who is busy or why.</p>
      </section>

      {p.status === "OPEN" &&
        p.slots.map((slot, index) => (
          <section key={slot.start_at} className="card">
            <h3>
              {index + 1}. {day(slot.start_at)} · {span(slot.start_at, slot.end_at)}
            </h3>
            <p className="hint">
              {slot.free} of {slot.of} free
            </p>
            {p.organiser && (
              <button type="button" className="on big" disabled={busy} onClick={() => pick(index)}>
                Create event at this time
              </button>
            )}
          </section>
        ))}

      {p.organiser && p.status === "COLLECTING" && (
        <section className="card organiser">
          <h2>You're the organiser</h2>
          {p.people && (
            <>
              <h3>Filled in ({p.people.done.length})</h3>
              <p className={p.people.done.length ? "" : "hint"}>{p.people.done.join(", ") || "Nobody yet."}</p>
              <h3>Not yet ({p.people.waiting.length})</h3>
              <p className={p.people.waiting.length ? "" : "hint"}>{p.people.waiting.join(", ") || "Everyone we know of has filled in."}</p>
              <p className="hint">Only people who have used AmIFree in this group are listed.</p>
            </>
          )}
          <div className="row">
            <button type="button" className="on" disabled={busy} onClick={() => run(() => api.suggest(p.id))}>
              See best times now
            </button>
            <button type="button" disabled={busy} onClick={() => run(() => api.nudgePoll(p.id))}>
              Remind people who haven't filled in
            </button>
          </div>
          <p className="hint">
            No need to wait here: the best times also appear by themselves when the deadline passes
            {p.joined < p.min_joined ? `, once at least ${p.min_joined} people have filled in` : ""}.
          </p>
        </section>
      )}

      {p.organiser && p.status !== "CLOSED" && (
        <section className="card organiser">
          <h2>Choose a time yourself</h2>
          <ChooseTime pollId={p.id} />
        </section>
      )}

      {p.joined_by_me && p.status !== "CLOSED" && (
        <details>
          <summary>{p.organiser ? "See all times" : "Who's free when"}</summary>
          <Heatmap />
        </details>
      )}
    </>
  );
}

const STEPS = ["Everyone fills in", "Best times shown", "Time chosen"];

/** Where the poll is up to, so nobody has to read a paragraph to find out. */
function Steps({ status }: { status: PollDetail["status"] }) {
  const current = status === "COLLECTING" ? 0 : status === "OPEN" ? 1 : 2;
  return (
    <ol className="steps" aria-label="Poll progress">
      {STEPS.map((label, index) => {
        const state = index < current || status === "CLOSED" ? "done" : index === current ? "now" : "later";
        return (
          <li key={label} className={state} aria-current={state === "now" ? "step" : undefined}>
            <span className="step-dot" aria-hidden="true">{state === "done" ? "✓" : index + 1}</span>
            {label}
          </li>
        );
      })}
    </ol>
  );
}

/** One line: what this person should do now, or that they're done. */
function NextStep({ poll: p }: { poll: PollDetail }) {
  if (p.status === "CLOSED") return <p><strong>All done.</strong> The time is chosen and posted in the group.</p>;
  if (p.status === "OPEN") {
    return p.organiser ? (
      <p><strong>Your turn:</strong> pick one of the times below. It's posted in the group so people can say Going.</p>
    ) : (
      <p><strong>You're done for now.</strong> The organiser is choosing the time, and it will be posted in the group.</p>
    );
  }
  if (!p.joined_by_me) return <p><strong>Your turn:</strong> mark when you're free so the group can find a time.</p>;
  return p.organiser ? (
    <p>
      <strong>You're in.</strong> Now wait for others to fill in. The best times appear here
      {p.deadline_at ? ` after ${day(p.deadline_at)}` : " when the deadline passes"}, and you pick one.
    </p>
  ) : (
    <p>
      <strong>You're in.</strong> Nothing else to do. When the deadline passes, the organiser picks a time and
      it's posted in the group.
    </p>
  );
}
