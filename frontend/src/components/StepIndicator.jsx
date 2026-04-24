/**
 * StepIndicator.jsx
 * Shows "Step N of 3" progress bar.
 */
import './StepIndicator.css';

const STEPS = ['Upload Images', 'Grid Editor', 'Review & Submit'];

export default function StepIndicator({ current }) {
  return (
    <div className="step-indicator">
      {STEPS.map((label, i) => {
        const step = i + 1;
        const done   = step < current;
        const active = step === current;
        return (
          <div key={step} className="step-item">
            <div className={`step-circle ${done ? 'step-done' : active ? 'step-active' : ''}`}>
              {done ? '✓' : step}
            </div>
            <span className={`step-label ${active ? 'step-label-active' : ''}`}>{label}</span>
            {i < STEPS.length - 1 && (
              <div className={`step-line ${done ? 'step-line-done' : ''}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}
