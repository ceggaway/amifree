import { api } from "../api";
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
  const clashes = plans.data.plans.filter((p) => p.availability.conflicts.length > 0).length;
  return (
    <>
      <section className="summary">
        <strong>{clashes === 0 ? "No clashes in your upcoming plans" : `${clashes} plan${clashes > 1 ? "s" : ""} with a clash`}</strong>
        <span className="hint">
          Checked against your Going plans, busy blocks
          {me.data?.nusmods.connected ? " and NUSMods timetable" : ". NUSMods isn't connected: send your link to the bot"}
          . Outside calendars aren't connected.
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
    </>
  );
}
