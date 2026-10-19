import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { day, span } from "../format";
import type { GroupEventSummary, GroupPoll } from "../types";
import { useLoad } from "../useLoad";

const MINE: Record<string, string> = { GOING: "You're going", MAYBE: "Maybe", DECLINED: "Can't", SAVED: "Saved" };

/** A group's events and open polls, with the two things people come here to do. */
export function GroupHome() {
  const { code = "" } = useParams();
  const { data, error } = useLoad(() => api.group(code), [code]);
  if (error) return <NotInGroup error={error} />;
  if (!data) return <p className="hint">Loading…</p>;
  return (
    <>
      <header className="group-head">
        <p className="hint">Group</p>
        <h1>{data.title ?? "This group"}</h1>
      </header>
      <div className="actions">
        <Link className="action" to={`/g/${code}/new`}>
          <span aria-hidden>📅</span> Create an event
        </Link>
        <Link className="action" to={`/g/${code}/find`}>
          <span aria-hidden>🗳</span> Find a time
        </Link>
      </div>
      <h2>Coming up</h2>
      {data.events.length === 0 && <p className="hint">No events yet. Create the first one above.</p>}
      {data.events.map((event) => (
        <EventRow key={event.id} event={event} />
      ))}
      {data.polls.length > 0 && <h2>Finding a time</h2>}
      {data.polls.map((poll) => (
        <PollRow key={poll.id} poll={poll} />
      ))}
    </>
  );
}

function EventRow({ event }: { event: GroupEventSummary }) {
  const { GOING, MAYBE } = event.counts;
  return (
    <Link className="card link" to={`/e/${event.id}`}>
      <div className="row between">
        <h3>{event.title}</h3>
        {event.my_status && <span className={`chip ${event.my_status.toLowerCase()}`}>{MINE[event.my_status]}</span>}
      </div>
      <p>
        {day(event.start_at)} · {span(event.start_at, event.end_at)}
        {event.location ? ` · ${event.location}` : ""}
      </p>
      <p className="hint">
        {GOING} going{MAYBE ? ` · ${MAYBE} maybe` : ""}
        {event.recurring ? " · 🔁 repeats" : ""}
        {event.organiser ? " · you organise this" : ""}
      </p>
    </Link>
  );
}

function PollRow({ poll }: { poll: GroupPoll }) {
  return (
    <Link className="card link" to={`/poll/${poll.id}`}>
      <h3>🗳 {poll.title}</h3>
      <p className="hint">
        {poll.joined} joined
        {poll.deadline_at ? ` · reply by ${day(poll.deadline_at)}` : ""}
        {poll.status === "OPEN" ? " · times suggested" : ""}
      </p>
      <p>{poll.status === "OPEN" ? "Answer ›" : poll.joined_by_me ? "You're in ›" : "Count me in ›"}</p>
    </Link>
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
