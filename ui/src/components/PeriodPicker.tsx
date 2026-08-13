import { PERIOD_PRESETS, resolveClientPeriod, type PeriodPreset } from "../lib/period";

export interface PeriodValue {
  preset: PeriodPreset;
  month: string;
  since: string;
  until: string;
}

export function PeriodPicker({
  months,
  value,
  onChange,
}: {
  months: string[];
  value: PeriodValue;
  onChange: (next: PeriodValue) => void;
}) {
  const resolved = resolveClientPeriod({ ...value, months });

  function setPreset(preset: PeriodPreset) {
    if (preset === "custom") {
      onChange({
        preset,
        month: value.month,
        since: resolved.since || value.since,
        until: resolved.until || value.until,
      });
      return;
    }
    onChange({ ...value, preset, month: preset === "month" ? "" : value.month });
  }

  return (
    <div className="period-picker">
      <div className="preset-row">
        {PERIOD_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className={`btn subtle small${value.preset === preset.id ? " selected" : ""}`}
            onClick={() => setPreset(preset.id)}
          >
            {preset.label}
          </button>
        ))}
      </div>
      {value.preset !== "custom" ? (
        <label>
          As of
          <select
            value={value.month || months[months.length - 1] || ""}
            onChange={(event) => onChange({ ...value, month: event.target.value })}
            disabled={!months.length}
          >
            {months.length === 0 && <option value="">No months yet</option>}
            {[...months].reverse().map((month) => (
              <option key={month} value={month}>
                {month}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <>
          <label>
            From
            <input
              type="date"
              value={value.since}
              onChange={(event) => onChange({ ...value, since: event.target.value })}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={value.until}
              onChange={(event) => onChange({ ...value, until: event.target.value })}
            />
          </label>
        </>
      )}
    </div>
  );
}
