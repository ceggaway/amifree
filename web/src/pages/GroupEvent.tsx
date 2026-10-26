import { Icon } from "../components/Icon";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, refusal } from "../api";
import { ClashBanner } from "../components/ClashBanner";
import { day, span, toInput, fromInput } from "../format";
import { closeApp, confirm, haptic } from "../telegram";
import type { Answer, GroupEventDetail } from "../types";
import { useLoad } from "../useLoad";
import { NotInGroup } from "./GroupHome";

const ANSWERS: [Answer, string][] = [
  ["GOING", "Going"],
  ["DECLINED", "Can't"],
];

/** One group event: answer, see who's going, check in, and (organisers) run it. */
export function GroupEvent() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const loaded = useLoad(() => api.groupEvent(id), [id]);
  const [event, setEvent] = useState<GroupEventDetail | null>(null);
  const [note, setNote] = useState(params.get("posted") ? "Posted in the group." : "");
  const [error, setError] = useState("");
  const [clash, setClash] = useState(false);
  const current = event ?? loaded.data;

  if (loaded.error) return <NotInGroup error={loaded.error} />;
  if (!current) return <p className="hint">Loading…</p>;
  const e = current;

  async function run(work: () => Promise<GroupEventDetail | { event: GroupEventDetail; message?: string }>, done?: string) {
    setError("");
    try {
      const result = await work();
      setEvent("event" in result ? result.event : result);
      if ("message" in result && result.message) setNote(result.message);
      else if (done) setNote(done);
      haptic("success");
    } catch (err) {
      const why = refusal(err);
      haptic(why.reason === "clash" ? "warning" : "error");
      if (why.reason === "clash") setClash(true);
      else setError(why.message);
      if (why.reason === "stale") loaded.reload();
    }
  }

  const answer = (status: Answer, force = false) => {
    setClash(false);
    return run(() => api.rsvp(e.id, status, force));
  };

  const live = e.status === "ACTIVE" && !e.ended;
  return (
    <>
      <p>
        <Link to={`/g/${e.chat}`}>‹ Back to group</Link>
      </p>
      <h1>{e.title}</h1>
      <p>
        {day(e.start_at)} · {span(e.start_at, e.end_at)}
      </p>
      {e.location && <p className="icon-label"><Icon name="pin" size={16} />{e.location}</p>}
      {e.notes && <p className="pre hint">{e.notes}</p>}
      {e.status === "CANCELLED" && <p className="banner clash">This event was cancelled.</p>}
      {e.ended && <p className="banner info">This event has ended.</p>}
      {note && (
        <p className="banner ok">
          {note}{" "}
          {note.startsWith("Posted") && (
            <button type="button" className="link-button" onClick={closeApp}>
              Back to the chat
            </button>
          )}
        </p>
      )}

      {live && (
        <section className="card">
          <h2 className="answer-heading">Are you going?</h2>
          <div className="segmented" role="group" aria-label="Your answer">
            {ANSWERS.map(([status, label]) => (
              <button key={status} type="button" className={e.my_status === status ? "on" : ""} aria-pressed={e.my_status === status} onClick={() => answer(status)}>
                {label}
              </button>
            ))}
          </div>
          <p className="hint">Your schedule at that time (only you see this):</p>
          <ClashBanner availability={e.availability} />
          {clash && (
            <div className="banner clash">
              That clashes with something you have.{" "}
              <button type="button" className="link-button" onClick={() => answer("GOING", true)}>
                Going anyway
              </button>
            </div>
          )}
          {e.recurring && (
            <button type="button" onClick={() => run(() => api.goingToAll(e.id))}>
              Going to every session
            </button>
          )}
          {e.check_in_open && (
            <button type="button" className={e.checked_in ? "on" : ""} disabled={e.checked_in} onClick={() => run(() => api.checkIn(e.id), "Checked in. Thanks for coming.")}>
              {e.checked_in ? "Checked in" : "I'm here"}
            </button>
          )}
        </section>
      )}

      <h2>
        {e.counts.GOING} going · {e.counts.DECLINED} can't
      </h2>
      {e.going.length > 0 ? <p>{e.going.join(", ")}</p> : <p className="hint">Nobody has said Going yet.</p>}

      {e.organiser && e.manage && <Organiser event={e} run={run} />}
      {error && <p className="error">{error}</p>}
    </>
  );
}

type Run = (work: () => Promise<GroupEventDetail | { event: GroupEventDetail; message?: string }>, done?: string) => Promise<void>;

function Organiser({ event: e, run }: { event: GroupEventDetail; run: Run }) {
  const [editing, setEditing] = useState(false);
  const [followup, setFollowup] = useState<"all" | "stop" | null>(null);
  const [form, setForm] = useState({
    title: e.title,
    start_at: toInput(e.start_at),
    end_at: toInput(e.end_at),
    location: e.location ?? "",
    notes: e.notes ?? "",
  });
  const manage = e.manage!;
  const { summary } = manage;
  const live = e.status === "ACTIVE" && !e.ended;
  const untilStart = new Date(e.start_at).getTime() - Date.now();
  // Confirmations go out the day before; check-in opens 30 minutes before.
  const soon = untilStart <= 30 * 3600 * 1000;
  const started = untilStart <= 30 * 60 * 1000;

  const message = (work: () => Promise<{ message: string }>) =>
    run(async () => ({ event: e, message: (await work()).message }));

  return (
    <section className="card organiser">
      <h2>You're the organiser</h2>
      {live && !editing && (
        <div className="row">
          <button type="button" onClick={() => setEditing(true)}>Edit</button>
          <button type="button" onClick={() => message(() => api.nudge(e.id))}>Remind people who haven't answered</button>
          <button
            type="button"
            className="danger"
            onClick={async () =>
              (await confirm("Cancel this event for everyone?")) &&
              run(async () => {
                const done = await api.cancelEvent(e.id, e.version);
                if (done.series_followup) setFollowup("stop");
                return { event: done.event, message: "Cancelled. Everyone who answered is told." };
              })
            }
          >
            Cancel event
          </button>
        </div>
      )}
      {editing && (
        <form
          className="stack"
          onSubmit={(ev) => {
            ev.preventDefault();
            run(async () => {
              const done = await api.editEvent(e.id, {
                version: e.version,
                title: form.title,
                start_at: fromInput(form.start_at),
                end_at: fromInput(form.end_at),
                location: form.location || null,
                notes: form.notes || null,
              });
              setEditing(false);
              if (done.series_followup) setFollowup("all");
              return { event: done.event, message: "Updated. Everyone who answered gets one message." };
            });
          }}
        >
          <input aria-label="Title" value={form.title} maxLength={200} required onChange={(ev) => setForm({ ...form, title: ev.target.value })} />
          <input aria-label="Starts" type="datetime-local" value={form.start_at} required onChange={(ev) => setForm({ ...form, start_at: ev.target.value })} />
          <input aria-label="Ends" type="datetime-local" value={form.end_at} required onChange={(ev) => setForm({ ...form, end_at: ev.target.value })} />
          <input aria-label="Where" placeholder="Where" value={form.location} maxLength={300} onChange={(ev) => setForm({ ...form, location: ev.target.value })} />
          <textarea aria-label="Notes" placeholder="Notes" value={form.notes} maxLength={1000} rows={2} onChange={(ev) => setForm({ ...form, notes: ev.target.value })} />
          <div className="row">
            <button type="submit" className="on">Save changes</button>
            <button type="button" onClick={() => setEditing(false)}>Keep unchanged</button>
          </div>
        </form>
      )}
      {followup && (
        <div className="banner info">
          This is one session of a repeating event.{" "}
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setFollowup(null);
              message(() => api.series(e.id, followup));
            }}
          >
            {followup === "all" ? "Change the later sessions to match" : "Cancel all later sessions too"}
          </button>
        </div>
      )}

      {!soon && (
        <p className="hint">
          {summary.going} going so far. The day before, AmIFree asks them to confirm, and check-in opens 30 minutes
          before the start. Those tools appear here then.
        </p>
      )}
      {soon && (
        <>
          <h3>Still coming?</h3>
          <p className="hint">{`${summary.confirmed} confirmed · ${summary.dropped} dropped · ${summary.no_reply} no reply`}</p>
        </>
      )}
      {started && (
        <>
          <h3>Who came ({manage.came})</h3>
          {manage.people.length === 0 ? (
            <p className="hint">Nobody has answered yet.</p>
          ) : (
            <>
              <ul className="people">
                {manage.people.map((person) => (
                  <li key={person.id}>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={person.present}
                        onChange={(ev) => run(() => api.markAttendance(e.id, person.id, ev.target.checked))}
                      />
                      {person.name}
                      <span className="hint"> · {LABEL[person.answer ?? ""] ?? "checked in"}</span>
                    </label>
                  </li>
                ))}
              </ul>
              <button type="button" onClick={() => run(() => api.markAttendance(e.id, null), "Marked everyone who said Going.")}>
                Everyone who said Going came
              </button>
            </>
          )}
        </>
      )}
    </section>
  );
}

const LABEL: Record<string, string> = { GOING: "Going", MAYBE: "Maybe", DECLINED: "Can't", SAVED: "Saved" };
