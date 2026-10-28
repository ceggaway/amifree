import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { day, shiftKey, startOfDay, todayKey } from "../format";
import { haptic } from "../telegram";
import { forget } from "../useLoad";

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

const RANGES = [
  ["next 7 days", "Next 7 days"],
  ["this week", "This week"],
  ["next week", "Next week"],
  ["weekend", "This weekend"],
  ["custom", "Pick dates"],
] as const;

/** Monday (as a date key) of the week holding `key`. */
const monday = (key: string) => shiftKey(key, -((new Date(`${key}T12:00:00+08:00`).getUTCDay() + 6) % 7));

/** The first and last day a poll checks for each choice. */
function range(choice: string): [string, string] {
  const today = todayKey();
  const week = monday(today);
  if (choice === "this week") return [today, shiftKey(week, 6)];
  if (choice === "next week") return [shiftKey(week, 7), shiftKey(week, 13)];
  if (choice === "weekend") {
    // This Sat–Sun; on a Sunday, just today. (Date keys compare correctly as strings.)
    const saturday = shiftKey(week, 5);
    return [saturday < today ? today : saturday, shiftKey(week, 6)];
  }
  return [shiftKey(today, 1), shiftKey(today, 7)];
}

/** The last day replies can close: the day before the first day, so every day asked about can still be chosen. */
const lastReplyDay = (first: string) => shiftKey(first, -1);

/** The /whosfree poll from a form: members add their times, the best slots are suggested. */
export function FindTime() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const [requestId] = useState(() => crypto.randomUUID());
  const [choice, setChoice] = useState("next 7 days");
  const [[first, last], setRange] = useState(() => range("next 7 days"));
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [customLength, setCustomLength] = useState(false);
  const [replyBy, setReplyBy] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // The server closes replies by the day before the first day. A first day of today leaves no
  // such day: replies then close a few hours after posting.
  const replyDay = lastReplyDay(first);
  const canChoose = replyDay >= todayKey();
  const chosenReply = canChoose && replyBy && replyBy <= replyDay ? replyBy : "";

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
        reply_by: chosenReply || null,
      });
      haptic("success");
      forget(`group:${code}`);
      // The organiser's own times come first, so the poll starts with one answer.
      navigate(`/poll/${made.id}/mine`, { replace: true, state: { justPosted: true } });
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h1>Find a time together</h1>
      <label>
        What for
        <input value={title} maxLength={200} required placeholder="Project meeting" onChange={(e) => setTitle(e.target.value)} />
      </label>
      <fieldset>
        <legend>How long?</legend>
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
      <fieldset>
        <legend>Which days?</legend>
        <div className="row">
          {RANGES.map(([name, label]) => (
            <button
              key={name}
              type="button"
              className={`chip-button${choice === name ? " on" : ""}`}
              aria-pressed={choice === name}
              onClick={() => {
                setChoice(name);
                if (name !== "custom") setRange(range(name));
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {choice === "custom" ? (
          <div className="pair">
            <label>
              From
              <input type="date" value={first} min={todayKey()} required onChange={(e) => setRange([e.target.value, last < e.target.value ? e.target.value : last])} />
            </label>
            <label>
              To
              <input type="date" value={last} min={first} max={shiftKey(first, 13)} required onChange={(e) => setRange([first, e.target.value])} />
            </label>
          </div>
        ) : (
          <p className="hint">
            {first === last ? day(startOfDay(first)) : `${day(startOfDay(first))} to ${day(startOfDay(last))}`}
          </p>
        )}
      </fieldset>
      {canChoose ? (
        <label>
          <span>
            Reply by <span className="hint">(optional)</span>
          </span>
          <input type="date" value={chosenReply} min={todayKey()} max={replyDay} onChange={(e) => setReplyBy(e.target.value)} />
          <span className="hint">
            Leave blank for {day(startOfDay(replyDay))}, 11:59pm. Replies close before the first day, so every day can still be chosen.
          </span>
        </label>
      ) : (
        <p className="hint">Replies close 3 hours after you post. Times before then are skipped.</p>
      )}
      {error && <p className="error">{error}</p>}
      <button type="submit" className="on big" disabled={busy}>
        {busy ? "Posting…" : "Post the poll to the group"}
      </button>
    </form>
  );
}
