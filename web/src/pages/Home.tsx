import { useEffect, useState } from "react";
import { api, refusal } from "../api";
import { Switch } from "../components/Switch";
import { haptic } from "../telegram";
import { PlanCard } from "../components/PlanCard";
import { addDays, dayKey } from "../format";
import { useLoad } from "../useLoad";

export function Home() {
  const me = useLoad(api.me);
  const plans = useLoad(api.plans);
  if (plans.error) return <p className="error">{plans.error}</p>;
  if (!plans.data) return <p className="hint">Loading…</p>;
  const today = dayKey(new Date());
  const tomorrow = dayKey(addDays(new Date(), 1));
  const on = (key: string) => plans.data!.plans.filter((p) => dayKey(p.start_at) === key);
  const calendars = me.data?.calendars ?? [];
  const broken = calendars.filter((c) => c.status === "ERROR").length;
  const calendarNote = me.data
    ? me.data.outside_calendars_connected
      ? broken > 0
        ? ` and your connected calendar${calendars.length > 1 ? "s" : ""} (${broken} can't be read: check /calendar in the bot)`
        : " and your connected calendar" + (calendars.length > 1 ? "s" : "")
      : calendars.length > 0
        ? ". Your calendar link isn't working: check /calendar in the bot"
        : ". Outside calendars aren't connected: send a calendar link to the bot"
    : "";
  const clashes = plans.data.plans.filter((p) => p.availability.conflicts.length > 0).length;
  return (
    <>
      <section className="summary">
        <strong>{clashes === 0 ? "No clashes in your upcoming plans" : `${clashes} plan${clashes > 1 ? "s" : ""} with a clash`}</strong>
        <span className="hint">
          Checked against your Going plans, busy blocks
          {me.data?.nusmods.connected ? ", NUSMods timetable" : ", no NUSMods timetable (send your link to the bot)"}
          {calendarNote}.
        </span>
      </section>
      {[["Today", today], ["Tomorrow", tomorrow]].map(([label, key]) => (
        <section key={key}>
          <h2>{label}</h2>
          {on(key).length === 0 && <p className="hint">Nothing saved.</p>}
          {on(key).map((p) => (
            <PlanCard key={p.id} plan={p} compact />
          ))}
        </section>
      ))}
      {me.data && <Reminders initial={me.data.reminders_enabled} />}
    </>
  );
}

function Reminders({ initial }: { initial: boolean }) {
  const [on, setOn] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setOn(initial), [initial]);
  async function change(next: boolean) {
    setBusy(true);
    setError("");
    setOn(next); // feels instant; put back if the server says no
    try {
      setOn((await api.setReminders(next)).reminders_enabled);
      haptic("success");
    } catch (err) {
      setOn(!next);
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h2>Settings</h2>
      <div className="card">
        <Switch
          label="🔔 Reminders"
          hint={
            on
              ? "On: the night-before summary, deadline reminders, \"still coming?\" checks and nudges about group plans you haven't answered."
              : "Off: none of those. Alerts when a plan you saved changes, and reminders you set with /remind, still arrive."
          }
          on={on}
          busy={busy}
          onChange={change}
        />
        {error && <p className="error">{error}</p>}
      </div>
    </section>
  );
}
