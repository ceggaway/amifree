import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { day, shiftKey, startOfDay, todayKey } from "../format";
import { MultiDatePicker } from "../components/MultiDatePicker";
import { haptic } from "../telegram";
import { forget } from "../useLoad";

const LENGTHS = [
  [30, "30 min"],
  [60, "1 hour"],
  [90, "1.5 hours"],
  [120, "2 hours"],
] as const;

const RANGES = [
  ["next 7 days", "Next 7 days"],
  ["weekend", "This weekend"],
  ["custom", "Choose dates"],
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
  // Starts the day after tomorrow: replies close the night before, so members get at least a day.
  return [shiftKey(today, 2), shiftKey(today, 8)];
}

/** The last day replies can close: the day before the first day, so every day asked about can still be chosen. */
const lastReplyDay = (first: string) => shiftKey(first, -1);

/** The /whosfree poll from a form: members add their times, the best slots are suggested. */
export function FindTime() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const [requestId] = useState(() => crypto.randomUUID());
  const [choice, setChoice] = useState("next 7 days");
  const [[rangeFirst, rangeLast], setRange] = useState(() => range("next 7 days"));
  const [selectedDates, setSelectedDates] = useState<string[]>([]);
  const first = choice === "custom" && selectedDates.length ? selectedDates[0] : rangeFirst;
  const last = choice === "custom" && selectedDates.length ? selectedDates[selectedDates.length - 1] : rangeLast;
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [customLength, setCustomLength] = useState(false);
  const [customHours, setCustomHours] = useState("1");
  const [customMinutes, setCustomMinutes] = useState("0");
  const hours = Number(customHours);
  const remainder = Number(customMinutes);
  const customTotal = hours * 60 + remainder;
  const customInvalid = !Number.isInteger(hours) || !Number.isInteger(remainder)
    || hours < 0 || hours > 8 || remainder < 0 || remainder > 59
    || customTotal < 30 || customTotal > 480;
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
    if (customLength && customInvalid) return;
    if (choice === "custom" && !selectedDates.length) {
      setError("Select at least one date.");
      return;
    }
    setBusy(true);
    try {
      const made = await api.createPoll(code, {
        request_id: requestId,
        title: title.trim(),
        minutes: customLength ? customTotal : minutes,
        first,
        last,
        reply_by: chosenReply || null,
        ...(choice === "custom" ? { selected_dates: selectedDates } : {}),
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
      <h1 className="feature-heading"><span className="feature-heading-icon" aria-hidden="true">🕒</span><span>Find a time</span></h1>
      <label>
        Purpose
        <input value={title} maxLength={200} required placeholder="Project meeting" onChange={(e) => setTitle(e.target.value)} />
      </label>
      <fieldset>
        <legend>Estimated time</legend>
        <div className="row duration-options">
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
            onClick={() => {
              if (!customLength) {
                setCustomHours(String(Math.floor(minutes / 60)));
                setCustomMinutes(String(minutes % 60));
              }
              setCustomLength(true);
            }}
          >
            Custom
          </button>
        </div>
        {customLength && (
          <div className="card custom-duration" role="group" aria-label="Custom duration">
            <div className="pair">
              <label>
                Hours
                <input type="number" inputMode="numeric" min={0} max={8} step={1}
                  value={customHours} onChange={(event) => setCustomHours(event.target.value)}
                  aria-describedby="custom-duration-help" />
              </label>
              <label>
                Minutes
                <input type="number" inputMode="numeric" min={0} max={59} step={1}
                  value={customMinutes} onChange={(event) => setCustomMinutes(event.target.value)}
                  aria-describedby="custom-duration-help" />
              </label>
            </div>
            <p id="custom-duration-help" className="hint">Total duration: 30 minutes to 8 hours.</p>
            {customInvalid && <p className="error" role="alert">Enter whole hours and minutes totalling 30 minutes to 8 hours.</p>}
          </div>
        )}
      </fieldset>
      <fieldset>
        <legend>Which days?</legend>
        <div className="row date-options">
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
          <MultiDatePicker value={selectedDates} onChange={setSelectedDates} />
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
        {busy ? "Posting…" : "Post to group"}
      </button>
    </form>
  );
}
