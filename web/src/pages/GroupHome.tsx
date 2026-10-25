import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { capitaliseFirst, dateNumber, day, month, span, weekday } from "../format";
import type { GroupEventSummary, GroupPoll } from "../types";
import { useLoad } from "../useLoad";

/** A group's events and open polls, with the two things people come here to do. */
export function GroupHome() {
  const { code = "" } = useParams();
  const { data, error } = useLoad(() => api.group(code), [code]);
  if (error) return <NotInGroup error={error} />;
  if (!data) return <p className="hint">Loading…</p>;
  const pending = data.polls.filter((poll) =>
    poll.status === "COLLECTING" ? !poll.availability_submitted : poll.organiser,
  );
  return (
    <>
      <header className="group-head">
        <h1>{data.title ?? "This group"}</h1>
      </header>
      <nav className="group-actions" aria-label="Plan together">
        <Link className="card link group-action" to={`/g/${code}/new`}>
          <span className="icon-tile"><Icon name="plus" /></span>
          <div>
            <h2>Create an event</h2>
            <p className="hint">Time decided? Set details and get RSVPs.</p>
          </div>
          <Icon name="arrow" size={18} />
        </Link>
        <Link className="card link group-action" to={`/g/${code}/find`}>
          <span className="icon-tile"><Icon name="clock" /></span>
          <div>
            <h2>Find a common time</h2>
            <p className="hint">Time undecided? Find a common time for the group.</p>
          </div>
          <Icon name="arrow" size={18} />
        </Link>
      </nav>
      {pending.length > 0 && (
        <section aria-labelledby="pending-action-heading">
          <h2 id="pending-action-heading">Pending action</h2>
          {pending.map((poll) => <PollRow key={poll.id} poll={poll} />)}
        </section>
      )}
      <h2>Upcoming event</h2>
      {data.events.length === 0 && <p className="hint">No upcoming events.</p>}
      {data.events.map((event) => (
        <EventRow key={event.id} event={event} />
      ))}
      <h2>Group tools</h2>
      <Link className="card link organiser-link" to={`/g/${code}/stats`}>
        <span className="icon-tile"><Icon name="chart" /></span>
        <div>
          <h3>Attendance and insights</h3>
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
  const action = poll.status === "COLLECTING" ? "Add your availability" : "Choose a time";
  return (
    <article className="card pending-card" aria-labelledby={`poll-${poll.id}-title`}>
      <h3 className="icon-label" id={`poll-${poll.id}-title`}><Icon name="calendar" size={18} />{title}</h3>
      {poll.status === "COLLECTING" && poll.deadline_at && (
        <p className="hint">Reply by {day(poll.deadline_at)}</p>
      )}
      <Link
        className="group-card-button"
        to={`/poll/${poll.id}${poll.status === "COLLECTING" ? "/mine" : ""}`}
        aria-label={`${action} for ${title}`}
      >
        {action}
      </Link>
    </article>
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
