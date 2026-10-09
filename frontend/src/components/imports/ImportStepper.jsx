export const STEPS = [
  ["upload", "Upload"],
  ["columns", "Match columns"],
  ["statuses", "Match statuses"],
  ["review", "Check"],
  ["done", "Done"],
];

export default function ImportStepper({ current }) {
  const index = STEPS.findIndex(([key]) => key === current);
  return (
    <ol className="stepper" aria-label="Import steps">
      {STEPS.map(([key, label], i) => (
        <li key={key} aria-current={key === current ? "step" : undefined} className={i < index ? "done" : key === current ? "current" : ""}>
          <span className="step-no">{i + 1}</span> {label}
        </li>
      ))}
    </ol>
  );
}
