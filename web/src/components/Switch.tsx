/** An on/off switch with its label and a one-line explanation. */
export function Switch({
  label,
  hint,
  on,
  busy,
  onChange,
}: {
  label: string;
  hint: string;
  on: boolean;
  busy?: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className="switch-row">
      <div>
        <strong>{label}</strong>
        <p className="hint">{hint}</p>
      </div>
      <button type="button" role="switch" aria-checked={on} aria-label={label} className={`switch${on ? " on" : ""}`} disabled={busy} onClick={() => onChange(!on)}>
        <span />
      </button>
    </div>
  );
}
