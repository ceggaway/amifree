import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { day, shiftKey, startOfDay, todayKey } from "../format";
import { haptic } from "../telegram";

const LENGTHS = [
  [30, "30 min"],
  [60, "1 hour"],
  [90, "1.5 hours"],
  [120, "2 hours"],
  [180, "3 hours"],
] as const;

/** 30 min to 8 hours in half-hour steps: the grid works in half hours and the server allows 8 hours. */
const CUSTOM_LENGTHS = Array.from({ length: 16 }, (_, index) => (index + 1) * 30);

const lengthLabel = (minutes: number) => {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} min`;
  return `${hours} hour${hours === 1 ? "" : "s"}${rest ? ` ${rest} min` : ""}`;
};

/** The poll checks the next 7 days, starting tomorrow. */
const range = (): [string, string] => [shiftKey(todayKey(), 1), shiftKey(todayKey(), 7)];

/** The deadline the server uses when none is chosen: halfway between now and the end of the last day. */
function defaultDeadline(last: string) {
  const now = Date.now();
  const end = startOfDay(shiftKey(last, 1)).getTime();
  return day(new Date(now + (end - now) / 2));
}

/** The /whosfree poll from a form: members add their times, the best slots are suggested. */
export function FindTime() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const [requestId] = useState(() => crypto.randomUUID());
  const [[first, last]] = useState(range);
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [customLength, setCustomLength] = useState(false);
  const [replyBy, setReplyBy] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const made = await api.createPoll(code, {
        request_id: requestId,
        title: title.trim(),
        minutes,
        first,
        last,
        reply_by: replyBy || null,
      });
      haptic("success");
      // The organiser's own times come first, so the poll starts with one answer.
      navigate(`/poll/${made.id}/mine`, { replace: true });
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h1>Find a time</h1>
      <p className="hint">
        Members fill in when they're free over the next 7 days ({day(startOfDay(first))} to {day(startOfDay(last))}),
        and AmIFree suggests the best times. The group only ever sees how many people are free, never who is busy.
      </p>
      <label>
        What for
        <input value={title} maxLength={200} required placeholder="Project meeting" onChange={(e) => setTitle(e.target.value)} />
      </label>
      <fieldset>
        <legend>How long is the meeting?</legend>
        <div className="row">
          {LENGTHS.map(([value, label]) => {
            const on = !customLength && minutes === value;
            return (
              <button
                key={value}
                type="button"
                className={`chip-button${on ? " on" : ""}`}
                aria-pressed={on}
                onClick={() => {
                  setCustomLength(false);
                  setMinutes(value);
                }}
              >
                {label}
              </button>
            );
          })}
          <button
            type="button"
            className={`chip-button${customLength ? " on" : ""}`}
            aria-pressed={customLength}
            onClick={() => setCustomLength(true)}
          >
            Custom
          </button>
        </div>
        {customLength && (
          <label>
            Length
            <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
              {CUSTOM_LENGTHS.map((value) => (
                <option key={value} value={value}>
                  {lengthLabel(value)}
                </option>
              ))}
            </select>
          </label>
        )}
      </fieldset>
      <label>
        <span>
          Deadline to fill in availabilities <span className="hint">(optional)</span>
        </span>
        <input type="date" value={replyBy} min={todayKey()} max={last} onChange={(e) => setReplyBy(e.target.value)} />
        <span className="hint">
          Members fill in their free times until the end of this day. Then AmIFree suggests the best times
          to the group to vote on. Leave blank to use {defaultDeadline(last)}.
        </span>
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" className="on big" disabled={busy}>
        {busy ? "Posting…" : "Post the poll to the group"}
      </button>
    </form>
  );
}
