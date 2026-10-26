import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { Icon } from "../components/Icon";
import { capitaliseFirst, dateNumber, day, month, span, weekday } from "../format";
import type { GroupEventSummary, GroupPoll } from "../types";
import { useLoad } from "../useLoad";

/** A group's events and open polls, with the two things people come here to do. */
export function GroupHome() {
  const { code = "" } = useParams();
  const { data, error } = useLoad(() => api.group(code), [code], `group:${code}`);
  if (error) return <NotInGroup error={error} />;
  if (!data) return <p className="hint">Loading…</p>;
  // Polls you still have to fill in, and every poll you organise (to follow its progress).
  const pending = data.polls.filter((poll) =>
    poll.status === "COLLECTING" ? !poll.availability_submitted || poll.organiser : poll.organiser,
  );
  return (
    <>
      <header className="group-head">
        <h1>{data.title ?? "This group"}</h1>
      </header>
      <Intro />
      {pending.length > 0 && (
        <section aria-labelledby="pending-action-heading">
          <h2 id="pending-action-heading">Needs you</h2>
          {pending.map((poll) => <PollRow key={poll.id} poll={poll} />)}
        </section>
      )}
      <nav className="group-actions" aria-labelledby="plan-heading">
        <p className="plan-question" id="plan-heading">Planning something? Do you know when?</p>
        <Link className="card link group-action" to={`/g/${code}/new`}>
          <span className="icon-tile"><Icon name="plus" /></span>
          <div>
            <h2>Yes, set the time</h2>
            <p className="hint">Post the event. Everyone taps Going or Can't.</p>
          </div>
          <Icon name="arrow" size={18} />
        </Link>
        <Link className="card link group-action" to={`/g/${code}/find`}>
          <span className="icon-tile"><Icon name="clock" /></span>
          <div>
            <h2>No, help us pick a time</h2>
            <p className="hint">Everyone marks when they're free. AmIFree finds the best time.</p>
          </div>
          <Icon name="arrow" size={18} />
        </Link>
      </nav>
      <h2>Coming up</h2>
      {data.events.length === 0 && (
        <p className="card empty">Nothing planned yet. Pick an option above to plan your first hangout.</p>
      )}
      {data.events.map((event) => (
        <EventRow key={event.id} event={event} />
      ))}
      <h2>Past events</h2>
      <Link className="card link organiser-link" to={`/g/${code}/stats`}>
        <span className="icon-tile"><Icon name="chart" /></span>
        <div>
          <h3>Attendance history</h3>
          <p className="hint">Who turned up to past events.</p>
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

function PollRow({ poll }: { poll: GroupPoll }) {
  const title = capitaliseFirst(poll.title);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const collecting = poll.status === "COLLECTING";
  const waiting = poll.waiting ?? 0;
  const mine = collecting && !poll.availability_submitted;
  const action = mine ? "Add your availability" : collecting ? "See who's filled in" : "Choose a time";
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

  return (
    <article className="card pending-card" aria-labelledby={`poll-${poll.id}-title`}>
      <h3 className="icon-label" id={`poll-${poll.id}-title`}><Icon name="calendar" size={18} />{title}</h3>
      {collecting && (
        <p className="hint">
          {poll.deadline_at ? `Reply by ${day(poll.deadline_at)}` : "Collecting free times"}
          {poll.organiser && ` · ${poll.joined} filled in${waiting ? ` · ${waiting} not yet` : ""}`}
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
      </div>
      {note && <p className="banner info">{note}</p>}
    </article>
  );
}

const INTRO_KEY = "amifree.group-intro-seen";

function seenIntro(): boolean {
  try {
    return localStorage.getItem(INTRO_KEY) === "1";
  } catch {
    return false;
  }
}

/** A one-time "how it works" for people opening a group for the first time. */
function Intro() {
  const [hidden, setHidden] = useState(seenIntro);
  if (hidden) return null;
  function dismiss() {
    try {
      localStorage.setItem(INTRO_KEY, "1");
    } catch {
      // Private mode: it just shows again next time.
    }
    setHidden(true);
  }
  return (
    <section className="card intro" aria-labelledby="intro-heading">
      <h2 id="intro-heading">How it works</h2>
      <ol>
        <li><strong>Plan something.</strong> Set a time, or let the group find one together.</li>
        <li><strong>Everyone answers in one tap</strong>, here or on the card in the chat.</li>
        <li><strong>AmIFree reminds people</strong>, so you don't have to chase anyone.</li>
      </ol>
      <p className="hint">Your schedule stays private. The group only sees how many people are free.</p>
      <button type="button" className="on" onClick={dismiss}>Got it</button>
    </section>
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
