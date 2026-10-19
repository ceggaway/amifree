import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { Icon } from "../components/Icon";
import { Switch } from "../components/Switch";
import { haptic } from "../telegram";
import { dateNumber, day, month, span } from "../format";
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
        <p className="eyebrow"><Icon name="users" size={14} /> Group workspace</p>
        <h1>{data.title ?? "This group"}</h1>
        <p className="hint">Your plans, people and time together.</p>
      </header>
      <div className="actions">
        <Link className="action" to={`/g/${code}/new`}>
          <Icon name="plus" size={18} /> Create an event
        </Link>
        <Link className="action secondary" to={`/g/${code}/find`}>
          <Icon name="clock" size={18} /> Find a time
        </Link>
      </div>
      <div className="section-heading">
        <h2>Coming up</h2>
        <span className="hint">{data.events.length} {data.events.length === 1 ? "event" : "events"}</span>
      </div>
      {data.events.length === 0 && <p className="hint">No events yet. Create the first one above.</p>}
      {data.events.map((event) => (
        <EventRow key={event.id} event={event} />
      ))}
      {data.polls.length > 0 && <h2>Finding a time</h2>}
      {data.polls.map((poll) => (
        <PollRow key={poll.id} poll={poll} />
      ))}
      <h2>For organisers</h2>
      <Link className="card link organiser-link" to={`/g/${code}/stats`}>
        <span className="icon-tile"><Icon name="chart" /></span>
        <div>
          <h3>Turnout and attendance</h3>
          <p className="hint">View event insights and export attendance.</p>
        </div>
        <Icon name="arrow" size={18} />
      </Link>
      {data.admin && <Detection code={code} on={data.detection} />}
    </>
  );
}

/** Admins only: let AmIFree spot plans in ordinary group messages. */
function Detection({ code, on: initial }: { code: string; on: boolean }) {
  const [on, setOn] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function change(next: boolean) {
    setBusy(true);
    setError("");
    try {
      setOn((await api.setDetection(code, next)).detection);
      haptic("success");
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card">
      <Switch
        label="Spot plans in chat"
        hint="AmIFree reads messages that mention a day and a time, once, and offers to make an event card. Text isn't stored. The group is told when this changes."
        on={on}
        busy={busy}
        onChange={change}
      />
      {on && <p className="hint">AmIFree must be an admin of the group to see messages.</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function EventRow({ event }: { event: GroupEventSummary }) {
  const { GOING } = event.counts;
  return (
    <Link className="card link event-row" to={`/e/${event.id}`}>
      <div className="date-tile" aria-hidden="true">
        <span>{month(event.start_at)}</span>
        <strong>{dateNumber(event.start_at)}</strong>
      </div>
      <div className="event-content">
        <div className="row between">
          <h3>{event.title}</h3>
          {event.my_status && <span className={`chip ${event.my_status.toLowerCase()}`}>{MINE[event.my_status]}</span>}
        </div>
        <p>
          {day(event.start_at)} · {span(event.start_at, event.end_at)}
          {event.location ? ` · ${event.location}` : ""}
        </p>
        <p className="hint">
          {GOING} going
          {event.recurring ? " · repeats" : ""}
          {event.organiser ? " · you organise this" : ""}
        </p>
      </div>
      <Icon name="arrow" size={16} />
    </Link>
  );
}

function PollRow({ poll }: { poll: GroupPoll }) {
  return (
    <Link className="card link" to={`/poll/${poll.id}`}>
      <h3 className="icon-label"><Icon name="clock" size={18} />{poll.title}</h3>
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
