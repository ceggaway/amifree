import { useEffect, useRef, useState } from "react";
import { confirm, haptic } from "../telegram";
import { Link, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { Icon } from "../components/Icon";
import { capitaliseFirst, dateNumber, day, month, span, weekday } from "../format";
import type { GroupEventSummary, GroupPoll } from "../types";
import { forget, useLoad } from "../useLoad";

/** A group's events and open polls, with the two things people come here to do. */
export function GroupHome() {
  const { code = "" } = useParams();
  const { data, error, reload } = useLoad(() => api.group(code), [code], `group:${code}`);
  if (error) return <NotInGroup error={error} />;
  if (!data) return <p className="hint">Loading…</p>;
  // Polls you still have to fill in, and every poll you organise (to follow its progress).
  const pending = data.polls.filter((poll) =>
    poll.status === "COLLECTING" ? !poll.availability_submitted || poll.organiser : poll.organiser,
  );
  return (
    <>
      <Intro />
      {pending.length > 0 && (
        <section className="pending-actions" aria-labelledby="pending-action-heading">
          <h2 id="pending-action-heading">Pending action</h2>
          {pending.map((poll) => <PollRow key={poll.id} poll={poll} onCancelled={reload} />)}
        </section>
      )}
      <nav className="group-actions" aria-label="Group planning">
        <Link className="card link group-action" to={`/g/${code}/new`}>
          <span className="icon-tile emoji" aria-hidden="true">📅</span>
          <div>
            <h2>Schedule an event</h2>
          </div>
          <Icon name="arrow" size={18} />
        </Link>
        <Link className="card link group-action" to={`/g/${code}/find`}>
          <span className="icon-tile emoji" aria-hidden="true">🕒</span>
          <div>
            <h2>Find a time</h2>
          </div>
          <Icon name="arrow" size={18} />
        </Link>
      </nav>
      <h2>Coming up</h2>
      {data.events.length === 0 && (
        <p className="card empty">Nothing planned yet.</p>
      )}
      {data.events.map((event) => (
        <EventRow key={event.id} event={event} />
      ))}
      <h2>Past events</h2>
      <Link className="card link organiser-link" to={`/g/${code}/stats`}>
        <span className="icon-tile"><Icon name="chart" /></span>
        <div>
          <h3>Attendance history</h3>
        </div>
        <Icon name="arrow" size={18} />
      </Link>
    </>
  );
}

function EventRow({ event }: { event: GroupEventSummary }) {
  const title = capitaliseFirst(event.title);
  return (
    <article className="card upcoming-card" aria-labelledby={`event-${event.id}-title`}>
      <time className="date-tile" dateTime={event.start_at} aria-label={day(event.start_at)}>
        <span aria-hidden="true">{month(event.start_at)}</span>
        <strong aria-hidden="true">{dateNumber(event.start_at)}</strong>
        <small className="date-weekday" aria-hidden="true">{weekday(event.start_at)}</small>
      </time>
      <div className="event-content">
        <h3 id={`event-${event.id}-title`}>{title}</h3>
        <p className="icon-label"><Icon name="clock" size={16} />{span(event.start_at, event.end_at)}</p>
        <Link className="group-card-button secondary" to={`/e/${event.id}`} aria-label={`Check details for ${title}`}>
          Check details
        </Link>
      </div>
    </article>
  );
}

function PollRow({ poll, onCancelled }: { poll: GroupPoll; onCancelled: () => void }) {
  const title = capitaliseFirst(poll.title);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const collecting = poll.status === "COLLECTING";
  const waiting = poll.waiting ?? 0;
  const mine = collecting && !poll.availability_submitted;
  const action = mine ? "Fill in my availabilities" : collecting ? "See who's filled in" : "Choose a time";
  const to = mine ? `/poll/${poll.id}/mine` : `/poll/${poll.id}`;

  async function remind() {
    setBusy(true);
    try {
      setNote((await api.nudgePoll(poll.id)).message);
    } catch (err) {
      setNote(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!(await confirm(`Cancel "${title}"? Everyone's answers are deleted.`))) return;
    setBusy(true);
    try {
      await api.cancelPoll(poll.id);
      haptic("success");
      forget(`poll:${poll.id}`);
      onCancelled();
    } catch (err) {
      haptic("error");
      setNote(refusal(err).message);
      setBusy(false);
    }
  }

  return (
    <article className="card pending-card" aria-labelledby={`poll-${poll.id}-title`}>
      <h3 className="icon-label" id={`poll-${poll.id}-title`}><Icon name="calendar" size={18} />{title}</h3>
      {collecting && (
        <p className="hint">
          {[
            poll.deadline_at && `Reply by ${day(poll.deadline_at)}`,
            poll.organiser && `${poll.joined} filled in${waiting ? ` · ${waiting} not yet` : ""}`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
      <div className="row">
        <Link className="group-card-button" to={to} aria-label={`${action} for ${title}`}>
          {action}
        </Link>
        {poll.organiser && collecting && waiting > 0 && (
          <button type="button" className="group-card-button secondary" disabled={busy} onClick={remind}>
            Remind the {waiting}
          </button>
        )}
        {poll.organiser && (
          <button type="button" className="group-card-button secondary danger" disabled={busy} onClick={cancel}>
            Cancel
          </button>
        )}
      </div>
      {note && <p className="banner info">{note}</p>}
    </article>
  );
}

const INTRO_KEY = "amifree.group-guide-hidden";
const INTRO_VISIT_KEY = "amifree.group-guide-dismissed";

function guidePreference(): boolean {
  try {
    return localStorage.getItem(INTRO_KEY) === "1";
  } catch {
    return false;
  }
}

function showGuide(): boolean {
  if (guidePreference()) return false;
  try {
    return sessionStorage.getItem(INTRO_VISIT_KEY) !== "1";
  } catch {
    return true;
  }
}

/** A dismissible welcome guide, available again from the group home page. */
function Intro() {
  const [open, setOpen] = useState(showGuide);
  const [hideForever, setHideForever] = useState(guidePreference);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
      return;
    }
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    dialog.querySelector("button")?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  function dismiss() {
    try {
      if (hideForever) localStorage.setItem(INTRO_KEY, "1");
      else localStorage.removeItem(INTRO_KEY);
    } catch {
      // The guide can still be closed when browser storage is unavailable.
    }
    try {
      sessionStorage.setItem(INTRO_VISIT_KEY, "1");
    } catch {
      // Keep the in-memory dismissal even if this visit cannot be remembered.
    }
    setOpen(false);
  }

  return (
    <>
      <div className="group-guide-link">
        <button type="button" className="guide-trigger" onClick={() => {
          setHideForever(guidePreference());
          setOpen(true);
        }}>
          <Icon name="info" size={18} /> Quick guide
        </button>
      </div>
      <dialog
        ref={dialogRef}
        className="group-guide"
        aria-labelledby="group-guide-heading"
        onCancel={(event) => {
          event.preventDefault();
          dismiss();
        }}
      >
        <h2 id="group-guide-heading">Welcome to the AmIFree Mini App</h2>
        <div className="guide-feature">
          <Icon name="calendar" size={24} />
          <p>
            <strong>Time already set?</strong><br />
            Choose <strong>Schedule an event</strong> to set the details and collect RSVPs.
          </p>
        </div>
        <div className="guide-feature">
          <Icon name="clock" size={24} />
          <p>
            <strong>Time undecided?</strong><br />
            Choose <strong>Find a time</strong> to collect availability,
            pick a slot and create your event.
          </p>
        </div>
        <label className="check guide-preference">
          <input type="checkbox" checked={hideForever} onChange={(event) => setHideForever(event.target.checked)} />
          Don’t show this again
        </label>
        <button type="button" className="on big guide-dismiss" onClick={dismiss}>Got it</button>
      </dialog>
    </>
  );
}

export function NotInGroup({ error }: { error: string }) {
  const hidden = /not found/i.test(error);
  return (
    <p className="error">
      {hidden ? "This group isn't available to you. Open AmIFree from a group you're in." : error}
    </p>
  );
}
